import os
import imaplib
import email
import json
import re
import smtplib
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, JobQueue
import anthropic

TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
CHAT_ID = int(os.environ['CHAT_ID'])

# Contas monitoradas
ACCOUNTS = [
    {
        'user': os.environ['GMAIL_USER'],
        'password': os.environ['GMAIL_APP_PASSWORD'],
        'label': '💼 Trabalho',
    },
    {
        'user': os.environ.get('GMAIL_USER_2', ''),
        'password': os.environ.get('GMAIL_APP_PASSWORD_2', ''),
        'label': '👤 Pessoal',
    },
]
ACCOUNTS = [a for a in ACCOUNTS if a['user'] and a['password']]

# Compat: manter GMAIL_USER/GMAIL_APP_PASSWORD apontando para a primeira conta
GMAIL_USER = ACCOUNTS[0]['user']
GMAIL_APP_PASSWORD = ACCOUNTS[0]['password']

# ─── ALERTAS IMEDIATOS ────────────────────────────────────────────────────────
# Palavras que disparam 🚨 ALERTA na hora, independente do horário

ALERT_KEYWORDS = [
    # Pessoas chave
    'fellipe guerra', 'vanessa', 'ipog',
    # Obrigações fiscais
    'sped', 'sped fiscal', 'sped contábil', 'sped contabil', 'efd',
    'ecf', 'ecd', 'reinf', 'esocial', 'e-social', 'dctf', 'gia', 'gim',
    'nfe', 'nf-e', 'nota fiscal', 'xml', 'danfe',
    # Clientes (nomes distintos da planilha)
    'flex wind', 'guerra advogados', 'guerra treinamentos',
    'growup', 'italtecnology', 'iqnus', 'kgary', 'gmad',
    'milk heroes', 'mlvl', 'mourao estrutura', 'novo frio',
    'fit & fresh', 'fg coworking', 'fg consultoria',
    'hbr logistica', 'heverline', 'gutemberg',
    'farmacia filadelfia', 'farmacia eri', 'm3 farmacia',
    'lead imobiliaria', 'loteamento portal',
    'jessica furtado', 'luana lima', 'ivone cordeiro',
    'franklin cunha', 'marluce pinheiro',
    'c4 comercio', 'c4 consultoria', 'az servicos',
    'instituto ect', 'instituto de educacao contabil',
    'deusamim', 'vg braga', 'braga reis',
    '4 estylos', 'erico brasil',
    # Remetentes específicos monitorados
    'cfla', 'cfla-intl', 'serviceclient@cfla', 't1134',
    'raquel brito', 'quellbrito@hotmail', 'rafaelabbrito',
    # Ações urgentes
    'intimação', 'intimacao', 'auto de infração', 'auto de infracao',
    'embargo', 'autuação', 'autuacao', 'notificação fiscal', 'notificacao fiscal',
    'multa de ofício', 'multa de oficio', 'parcelamento', 'execução fiscal',
    'penhora', 'bloqueio judicial', 'certidão negativa', 'certidao negativa',
    'regularize', 'pgfn', 'dívida ativa', 'divida ativa',
]

# ─── FILTROS ──────────────────────────────────────────────────────────────────

SPAM_SENDERS = [
    'netshoes', 'nordresearch', 'avast', 'cvc.com', 'skyscanner',
    'grancursosonline', 'serasa', 'smiles.com', 'facebookmail',
    'insiderstore', 'wispr.ai', 'academia-mail', 'hotmilhas',
    'retornar.com', 'accor', 'reminders@', 'news.all@mail',
    'grancursos', 'empiricus', 'infomoney',
]
SPAM_SUBJECTS = [
    '% off', 'cupom', 'oferta imperd', 'passagem', 'megapromo',
    'economize', 'desconto extra', 'prorrogamos', 'feirao', 'feirão',
    'voucher', 'milhas', 'resort', 'black friday',
    'ultima chance', 'última chance', 'imperdivel', 'imperdível',
]
URGENT_SENDERS = [
    'atendimento@compliance-ce.com.br', 'pgfn.gov.br', 'qive.com.br',
    'sigmavaf.com.br', 'accounts.google.com', 'adveronix.com',
    'regularize', 'receita.fazenda', 'flex-wind.com', 'tailscale',
    'sefaz', 'prefeitura', 'tce.', 'tribunal',
]
URGENT_SUBJECTS = [
    'gestta', 'vencer', 'certificado', 'regularize', 'seguranca',
    'segurança', 'prefeitura', 'pgfn', 'multa', 'vencimento',
    'prazo', 'pendencia', 'pendência', 'provisao', 'provisão',
    'reversao', 'reversão', 'trial ends', 'notificacao', 'notificação',
    'intimacao', 'intimação', 'auto de infração',
]
SALES_SENDERS = ['eduzz.com', 'hotmart.com']
PROFESSIONAL_SENDERS = [
    'conjur.com.br', 'legisweb.com.br', 'reformatributaria.com.br', 'fbc.org.br',
]
PERSONAL_SENDERS = ['colmaster.com.br', 'isabelbtz@gmail']
FINANCIAL_SENDERS = ['xpi.com.br', 'xpi.com', 'nubank', 'itau', 'bradesco', 'bb.com.br']

ICONS = {
    'URGENTE': '🔴',
    'VENDAS': '💰',
    'PROFISSIONAL': '👨‍💼',
    'PESSOAL': '👦',
    'FINANCEIRO': '💳',
    'OUTROS': '📌',
}

ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

# ─── REGRAS AUTOMÁTICAS ───────────────────────────────────────────────────────
# Quando email bate numa regra: alerta Telegram + encaminha automaticamente

RULES = [
    {
        'name': 'IPOG — Solicitação de NF',
        'match_from': ['nf@ipog.edu.br', 'ipog.edu.br'],
        'match_subject': ['solicitação nf', 'solicitacao nf', 'nota fiscal', 'nf semana'],
        'forward_to': ['vanessa2mlima@gmail.com', 'vanessalima@compliance-ce.com.br'],
        'forward_from_account': 'cnp',   # qual conta encaminhar (cnp = pessoal)
        'alert_msg': '🚨 IPOG — Solicitação de NF recebida!\nEncaminhado para Vanessa automaticamente.',
        'priority': 'URGENTE',
    },
    # Adicione mais regras aqui no futuro
]

# Controle de IDs já processados por regras (evita repetição)
_rule_processed_ids = set()

# Controle de alertas já enviados (evita repetição)
_alerted_ids = set()

# Controle de solicitações de parecer pendentes: uid -> email_data
_parecer_pending = {}

# ─── DETECÇÃO DE SOLICITAÇÕES DE PARECER ─────────────────────────────────────
# Palavras no ASSUNTO que sugerem consulta tributária/fiscal
PARECER_SUBJECT_KEYWORDS = [
    'fiscal', 'tributar', 'análise', 'analise', 'orientação', 'orientacao',
    'consulta', 'regime', 'cnae', 'icms', 'pis', 'cofins', 'posicionamento',
    'parecer', 'estrutura', 'enquadramento', 'filial', 'cnpj', 'nfc-e',
]

# Frases no CORPO que confirmam pedido de análise/orientação
PARECER_BODY_KEYWORDS = [
    'análise e orientação', 'analise e orientacao',
    'orientação sobre', 'orientacao sobre',
    'gostaríamos de receber', 'gostariamos de receber',
    'ficamos no seu aguardo', 'aguardamos seu retorno',
    'aguardo seu posicionamento', 'pedimos orientação', 'pedimos orientacao',
    'solicito orientação', 'solicito orientacao', 'solicito parecer',
    'posicionamento técnico', 'posicionamento tecnico',
    'necessidade de abertura', 'novo cnpj', 'nova filial',
    'adequação do regime', 'adequacao do regime',
    'regime tributário adequado', 'impacto tributário', 'impacto tributario',
    'detalhamento do projeto para análise', 'detalhamento do projeto para analise',
    'orientação técnica', 'orientacao tecnica',
    'parte fiscal', 'análise fiscal', 'analise fiscal',
    'estrutura tributária', 'estrutura tributaria',
    'icms-st', 'cnae adequado', 'obrigações tributárias', 'obrigacoes tributarias',
]


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def decode_str(s):
    if not s:
        return ''
    parts = decode_header(s)
    result = ''
    for part, enc in parts:
        if isinstance(part, bytes):
            result += part.decode(enc or 'utf-8', errors='replace')
        else:
            result += str(part)
    return result.strip()


def is_spam(sender, subject):
    s, sub = sender.lower(), subject.lower()
    return any(x in s for x in SPAM_SENDERS) or any(x in sub for x in SPAM_SUBJECTS)


def classify(sender, subject):
    s, sub = sender.lower(), subject.lower()
    if any(x in s for x in URGENT_SENDERS) or any(x in sub for x in URGENT_SUBJECTS):
        return 'URGENTE'
    if any(x in s for x in SALES_SENDERS):
        return 'VENDAS'
    if any(x in s for x in PROFESSIONAL_SENDERS):
        return 'PROFISSIONAL'
    if any(x in s for x in PERSONAL_SENDERS):
        return 'PESSOAL'
    if any(x in s for x in FINANCIAL_SENDERS):
        return 'FINANCEIRO'
    return 'OUTROS'


def match_alert(sender, subject):
    """Retorna o keyword que deu match, ou None."""
    combined = (sender + ' ' + subject).lower()
    for kw in ALERT_KEYWORDS:
        if kw in combined:
            return kw
    return None


def get_imap(user=None, password=None, readonly=True):
    user = user or GMAIL_USER
    password = password or GMAIL_APP_PASSWORD
    mail = imaplib.IMAP4_SSL('imap.gmail.com')
    mail.login(user, password)
    mail.select('inbox', readonly=readonly)
    return mail


def search_unseen(mail, hours=None):
    if hours:
        since = (datetime.now() - timedelta(hours=hours)).strftime('%d-%b-%Y')
        _, data = mail.search(None, f'(UNSEEN SINCE {since})')
    else:
        _, data = mail.search(None, 'UNSEEN')
    return data[0].split()


def fetch_headers(mail, ids, limit=200):
    results = []
    for eid in ids[-limit:]:
        _, msg_data = mail.fetch(eid, '(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])')
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)
        sender = decode_str(msg.get('From', ''))
        subject = decode_str(msg.get('Subject', '(sem assunto)'))
        results.append((eid, sender, subject))
    return results


def match_rule(sender, subject):
    """Retorna a regra que bate com o email, ou None."""
    s, sub = sender.lower(), subject.lower()
    for rule in RULES:
        from_match = any(x in s for x in rule['match_from'])
        subj_match = any(x in sub for x in rule['match_subject'])
        if from_match or subj_match:
            return rule
    return None


def forward_email(original_msg_bytes, rule, acc):
    """Encaminha o email via SMTP usando a conta configurada na regra."""
    try:
        # Seleciona a conta de envio
        send_acc = next(
            (a for a in ACCOUNTS if rule['forward_from_account'] in a['user']),
            ACCOUNTS[0]
        )
        # Monta email de encaminhamento
        original = email.message_from_bytes(original_msg_bytes)
        orig_from = decode_str(original.get('From', ''))
        orig_subject = decode_str(original.get('Subject', ''))
        orig_date = decode_str(original.get('Date', ''))
        orig_body = get_email_body(original)

        fwd = MIMEMultipart()
        fwd['From'] = send_acc['user']
        fwd['To'] = ', '.join(rule['forward_to'])
        fwd['Subject'] = f'Fwd: {orig_subject}'

        body = (
            f'-------- Mensagem encaminhada --------\n'
            f'De: {orig_from}\n'
            f'Data: {orig_date}\n'
            f'Assunto: {orig_subject}\n\n'
            f'{orig_body}'
        )
        fwd.attach(MIMEText(body, 'plain', 'utf-8'))

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(send_acc['user'], send_acc['password'])
            smtp.sendmail(send_acc['user'], rule['forward_to'], fwd.as_bytes())
        return True
    except Exception as e:
        print(f'Erro ao encaminhar email: {e}')
        return False


def get_email_body(msg):
    """Extrai o texto do corpo do email."""
    body = ''
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get('Content-Disposition', ''))
            if ct == 'text/plain' and 'attachment' not in cd:
                try:
                    body += part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', errors='replace')
                except:
                    pass
    else:
        try:
            body = msg.get_payload(decode=True).decode(msg.get_content_charset() or 'utf-8', errors='replace')
        except:
            body = str(msg.get_payload())
    return body.strip()[:3000]  # limite para API


def search_email_by_name(name_query):
    """Busca o email mais recente de um remetente pelo nome."""
    for acc in ACCOUNTS:
        try:
            mail = get_imap(acc['user'], acc['password'], readonly=True)
            _, data = mail.search(None, f'(FROM "{name_query}")')
            ids = data[0].split()
            if not ids:
                # Tenta busca parcial por palavra
                _, data = mail.search(None, 'ALL')
                all_ids = data[0].split()
                # Pega os últimos 200 e filtra por nome
                for eid in reversed(all_ids[-200:]):
                    _, msg_data = mail.fetch(eid, '(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])')
                    raw = msg_data[0][1]
                    msg = email.message_from_bytes(raw)
                    sender = decode_str(msg.get('From', ''))
                    if name_query.lower() in sender.lower():
                        ids = [eid]
                        break
            if ids:
                eid = ids[-1]  # mais recente
                _, msg_data = mail.fetch(eid, '(BODY.PEEK[])')
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)
                sender = decode_str(msg.get('From', ''))
                subject = decode_str(msg.get('Subject', ''))
                date = decode_str(msg.get('Date', ''))
                body = get_email_body(msg)
                mail.logout()
                return {'sender': sender, 'subject': subject, 'date': date, 'body': body, 'account': acc['label']}
            mail.logout()
        except Exception as e:
            print(f'Erro search_email_by_name: {e}')
    return None


def match_parecer_subject(subject):
    """Retorna True se o assunto sugere uma consulta tributária."""
    sub = subject.lower()
    return any(kw in sub for kw in PARECER_SUBJECT_KEYWORDS)


def match_parecer_body(body):
    """Retorna True se o corpo confirma pedido de orientação/análise."""
    b = body.lower()
    return any(kw in b for kw in PARECER_BODY_KEYWORDS)


def extract_empresa_cnpj(text):
    """Extrai nome da empresa e CNPJ do corpo do email."""
    empresa = None
    cnpj = None

    m = re.search(r'\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}', text)
    if m:
        cnpj = m.group()

    patterns = [
        r'empresa\s+([A-ZÀ-Úa-zà-ú][^\(,\n]{2,40}?)(?:\s*[\(,]|\s+CNPJ|\s+\(CNPJ)',
        r'([A-ZÀ-Ú][A-ZÀ-Úa-zà-ú\s]{2,30})\s*\(CNPJ',
        r'cliente\s+([A-ZÀ-Úa-zà-ú][^\(,\n]{2,40}?)(?:\s*[\(,])',
    ]
    for pat in patterns:
        m2 = re.search(pat, text, re.IGNORECASE)
        if m2:
            empresa = m2.group(1).strip().rstrip('.,')
            break

    return empresa, cnpj


def generate_parecer_claude(email_data):
    """Gera parecer preliminar via Claude Sonnet seguindo as 10 seções da Compliance."""
    if not claude_client:
        return None
    empresa = email_data.get('empresa', 'Cliente')
    cnpj = email_data.get('cnpj', '')
    hoje = datetime.now().strftime('%d/%m/%Y')

    prompt = f"""Você é Marcos Lima, contador tributário da Compliance-CE.

Analise o email abaixo e elabore um PARECER TÉCNICO PRELIMINAR seguindo exatamente as 10 seções:

1. CABEÇALHO
   Data: {hoje} | Empresa: {empresa} | CNPJ: {cnpj}
   Solicitante: {email_data['sender']} | Parecerista: Marcos Lima

2. QUESTIONAMENTO
   Contexte a consulta, as dúvidas levantadas e a base legal citada pelo cliente.

3. BASE LEGAL ATUALIZADA
   Liste artigos, incisos e alíneas aplicáveis. Formato: "LC 214/2025, art. 27, § 3º, inciso II, alínea a"

4. INTERPRETAÇÃO NORMATIVA
   Análise técnica da norma, hermenêutica jurídico-contábil, posicionamento doutrinário quando relevante.

5. IMPLICAÇÕES PRÁTICAS
   Consequências concretas para o contribuinte: impacto fiscal, operacional e contábil.

6. ORIENTAÇÕES OPERACIONAIS
   O que o cliente deve fazer: prazos, procedimentos, ajustes em sistemas e documentos.

7. VEDAÇÕES OU EXCEÇÕES
   Restrições legais, exceções à regra geral, riscos de autuação fiscal.

8. CITAÇÕES COMPLEMENTARES
   Jurisprudência (STJ, TRFs, CARF), Soluções de Consulta RFB, manifestações SEFAZ, doutrina.

9. CONCLUSÃO
   Síntese objetiva — resposta direta a cada pergunta do cliente.

10. ASSINATURA TÉCNICA
    Marcos Lima — CRC/CE | Data: {hoje}
    ⚠️ PARECER PRELIMINAR — sujeito a revisão antes da entrega ao cliente.

---
Email recebido:
De: {email_data['sender']}
Assunto: {email_data['subject']}

{email_data['body']}
---

Responda em português, com linguagem técnica tributária. Cite a legislação de forma completa e precisa."""

    msg = claude_client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=4000,
        messages=[{'role': 'user', 'content': prompt}]
    )
    return msg.content[0].text


def save_to_drive(content, empresa, cnpj=None):
    """Salva o parecer no Google Drive em Empresas/{empresa}/. Requer GOOGLE_SERVICE_ACCOUNT_JSON e DRIVE_EMPRESAS_FOLDER_ID."""
    sa_json = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON', '')
    folder_id = os.environ.get('DRIVE_EMPRESAS_FOLDER_ID', '')
    if not sa_json or not folder_id:
        return None
    try:
        import json as _json
        import io as _io
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseUpload

        creds = service_account.Credentials.from_service_account_info(
            _json.loads(sa_json),
            scopes=['https://www.googleapis.com/auth/drive']
        )
        service = build('drive', 'v3', credentials=creds)

        empresa_clean = re.sub(r'[^\w\s\-]', '', empresa).strip()[:50]

        results = service.files().list(
            q=f"name='{empresa_clean}' and '{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields='files(id)'
        ).execute()
        files = results.get('files', [])
        if files:
            subfolder_id = files[0]['id']
        else:
            meta = {'name': empresa_clean, 'mimeType': 'application/vnd.google-apps.folder', 'parents': [folder_id]}
            f = service.files().create(body=meta, fields='id').execute()
            subfolder_id = f['id']

        ts = datetime.now().strftime('%Y%m%d_%H%M')
        filename = f'PARECER_PRELIMINAR_{empresa_clean}_{ts}.txt'
        media = MediaIoBaseUpload(_io.BytesIO(content.encode('utf-8')), mimetype='text/plain')
        file_meta = {'name': filename, 'parents': [subfolder_id]}
        created = service.files().create(body=file_meta, media_body=media, fields='id,webViewLink').execute()
        return created.get('webViewLink')
    except Exception as e:
        print(f'Erro save_to_drive: {e}')
        return None


def analyze_with_claude(email_data):
    """Envia email para Claude e retorna resumo + plano de ação."""
    if not claude_client:
        return '❌ API Claude não configurada.'
    prompt = f"""Você é um assistente de um contador tributário brasileiro chamado Marcos Lima.

Analise o email abaixo e retorne:

1. RESUMO (máx 3 linhas): do que se trata o email
2. PLANO DE AÇÃO: lista numerada de ações concretas que Marcos deve tomar, em ordem de prioridade
3. PRAZO SUGERIDO: quando resolver (se identificável)
4. QUEM ENVOLVER: se precisar de mais alguém da equipe

Email:
De: {email_data['sender']}
Assunto: {email_data['subject']}
Data: {email_data['date']}

{email_data['body']}

Responda em português, de forma direta e prática. Sem introduções."""

    msg = claude_client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=600,
        messages=[{'role': 'user', 'content': prompt}]
    )
    return msg.content[0].text


def fetch_emails_account(acc, hours=None, only_cat=None, limit=100):
    mail = get_imap(acc['user'], acc['password'], readonly=True)
    ids = search_unseen(mail, hours)
    total_unseen = len(ids)
    buckets = {k: [] for k in ICONS}
    spam_count = 0
    for eid, sender, subject in fetch_headers(mail, ids, limit):
        if is_spam(sender, subject):
            spam_count += 1
            continue
        cat = classify(sender, subject)
        if only_cat and cat != only_cat:
            continue
        name = sender.split('<')[0].strip().strip('"') or sender
        name = name[:35] + '...' if len(name) > 35 else name
        sub = subject[:55] + '...' if len(subject) > 55 else subject
        buckets[cat].append(f'• {sub}\n  ↳ {name}')
    mail.logout()
    return buckets, total_unseen, spam_count


def fetch_emails(hours=None, only_cat=None, limit=100):
    try:
        merged = {k: [] for k in ICONS}
        total_all, spam_all = 0, 0
        for acc in ACCOUNTS:
            buckets, total, spam = fetch_emails_account(acc, hours, only_cat, limit)
            total_all += total
            spam_all += spam
            for cat in ICONS:
                # Prefixar com label da conta se mais de uma conta
                if len(ACCOUNTS) > 1:
                    merged[cat].extend([f'[{acc["label"]}] {item}' for item in buckets[cat]])
                else:
                    merged[cat].extend(buckets[cat])
        return merged, total_all, spam_all, None
    except Exception as e:
        return None, 0, 0, str(e)


def count_by_category(hours=None, limit=500):
    try:
        total_all = 0
        counts_all = {k: 0 for k in ICONS}
        spam_all = 0
        for acc in ACCOUNTS:
            mail = get_imap(acc['user'], acc['password'], readonly=True)
            ids = search_unseen(mail, hours)
            total_all += len(ids)
            for eid, sender, subject in fetch_headers(mail, ids, limit):
                if is_spam(sender, subject):
                    spam_all += 1
                    continue
                counts_all[classify(sender, subject)] += 1
            mail.logout()
        return total_all, counts_all, spam_all, None
    except Exception as e:
        return 0, {}, 0, str(e)


def mark_as_read(hours=None):
    try:
        total = 0
        for acc in ACCOUNTS:
            mail = get_imap(acc['user'], acc['password'], readonly=False)
            ids = search_unseen(mail, hours)
            if ids:
                for i in range(0, len(ids), 100):
                    batch = ids[i:i+100]
                    mail.store(b','.join(batch), '+FLAGS', '\\Seen')
            total += len(ids)
            mail.logout()
        return total
    except Exception as e:
        return -1


def build_message(buckets, title='📬 Emails não lidos'):
    lines = [title, '']
    total = 0
    for cat, items in buckets.items():
        if items:
            lines.append(f'{ICONS[cat]} {cat}')
            lines.extend(items)
            lines.append('')
            total += len(items)
    if total == 0:
        return '✅ Nenhum email importante no momento.'
    return '\n'.join(lines).strip()


# ─── MONITORAMENTO AUTOMÁTICO (a cada 10 min) ─────────────────────────────────

async def check_alerts(context):
    """Roda em background — verifica alertas e regras automáticas."""
    global _alerted_ids, _rule_processed_ids
    for acc in ACCOUNTS:
        try:
            mail = get_imap(acc['user'], acc['password'], readonly=True)
            since = (datetime.now() - timedelta(minutes=35)).strftime('%d-%b-%Y')
            _, data = mail.search(None, f'(UNSEEN SINCE {since})')
            ids = data[0].split()

            for eid in ids[-50:]:
                uid = f"{acc['user']}:{eid}"

                # Busca headers
                _, msg_data = mail.fetch(eid, '(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])')
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)
                sender = decode_str(msg.get('From', ''))
                subject = decode_str(msg.get('Subject', ''))

                # ── Verificar REGRAS automáticas ──
                rule = match_rule(sender, subject)
                if rule and uid not in _rule_processed_ids:
                    _rule_processed_ids.add(uid)
                    # Busca email completo para encaminhar
                    _, full_data = mail.fetch(eid, '(BODY.PEEK[])')
                    full_raw = full_data[0][1]
                    ok = forward_email(full_raw, rule, acc)
                    hora = datetime.now().strftime('%H:%M')
                    status = '✅ Encaminhado' if ok else '❌ Falha ao encaminhar'
                    destinos = '\n'.join(f'  • {d}' for d in rule['forward_to'])
                    msg_tg = (
                        f'{rule["alert_msg"]}\n\n'
                        f'📧 {subject}\n'
                        f'↳ {sender}\n'
                        f'🕐 {hora}\n\n'
                        f'{status} para:\n{destinos}'
                    )
                    await context.bot.send_message(chat_id=CHAT_ID, text=msg_tg)
                    continue  # já tratado pela regra

                # ── Verificar SOLICITAÇÕES DE PARECER ──
                if uid not in _alerted_ids and match_parecer_subject(subject):
                    # Busca corpo para confirmação e extração de empresa
                    try:
                        _, full_data = mail.fetch(eid, '(BODY.PEEK[])')
                        full_raw = full_data[0][1]
                        full_msg = email.message_from_bytes(full_raw)
                        body = get_email_body(full_msg)
                        date_str = decode_str(full_msg.get('Date', ''))

                        if match_parecer_body(body):
                            _alerted_ids.add(uid)
                            empresa, cnpj = extract_empresa_cnpj(body)
                            name = sender.split('<')[0].strip().strip('"') or sender
                            hora = datetime.now().strftime('%H:%M')

                            _parecer_pending[uid] = {
                                'sender': sender,
                                'subject': subject,
                                'body': body,
                                'date': date_str,
                                'empresa': empresa or 'Cliente',
                                'cnpj': cnpj or '',
                                'account': acc['label'],
                            }

                            empresa_info = f'\n🏢 Empresa: {empresa}' if empresa else ''
                            cnpj_info = f'\n📋 CNPJ: {cnpj}' if cnpj else ''
                            msg_tg = (
                                f'📝 SOLICITAÇÃO DE PARECER — {hora}\n'
                                f'{acc["label"]}{empresa_info}{cnpj_info}\n\n'
                                f'📧 {subject}\n'
                                f'↳ {name}\n\n'
                                f'💡 Responda "parecer" para gerar o parecer preliminar'
                            )
                            await context.bot.send_message(chat_id=CHAT_ID, text=msg_tg)
                            continue
                    except Exception as e:
                        print(f'Erro ao verificar parecer {uid}: {e}')

                # ── Verificar ALERTAS por palavra-chave ──
                if uid not in _alerted_ids:
                    kw = match_alert(sender, subject)
                    if kw:
                        _alerted_ids.add(uid)
                        name = sender.split('<')[0].strip().strip('"') or sender
                        hora = datetime.now().strftime('%H:%M')
                        msg_tg = (
                            f'🚨 ALERTA — {hora}\n'
                            f'{acc["label"]}\n\n'
                            f'Palavra-chave: {kw.upper()}\n\n'
                            f'📧 {subject}\n'
                            f'↳ {name}'
                        )
                        await context.bot.send_message(chat_id=CHAT_ID, text=msg_tg)

            mail.logout()
        except Exception as e:
            print(f'Erro check_alerts {acc["user"]}: {e}')


# ─── HANDLERS ────────────────────────────────────────────────────────────────

def only_authorized(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.id != CHAT_ID:
            return
        await func(update, context)
    return wrapper


@only_authorized
async def cmd_todos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('🔍 Contando TODOS os emails não lidos...')
    total, counts, spam, err = count_by_category(hours=None, limit=500)
    if err:
        await update.message.reply_text(f'❌ Erro: {err}')
        return
    lines = [f'📊 RESUMO GERAL — {total} emails não lidos\n']
    for cat, n in counts.items():
        if n > 0:
            lines.append(f'{ICONS[cat]} {cat}: {n}')
    lines.append(f'🗑️ Propaganda: {spam}')
    nao_contados = total - sum(counts.values()) - spam
    if nao_contados > 0:
        lines.append(f'⚠️ Além do limite analisado: ~{nao_contados}')
    lines.append('\nUse "urgente", "vendas" ou "hoje" para detalhes.')
    await update.message.reply_text('\n'.join(lines))


@only_authorized
async def cmd_emails(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('🔍 Emails das últimas 24h...')
    buckets, total, spam, err = fetch_emails(hours=24, limit=100)
    if err:
        await update.message.reply_text(f'❌ Erro: {err}')
        return
    await update.message.reply_text(build_message(buckets, f'📬 Hoje — {total} total, {spam} propagandas'))


@only_authorized
async def cmd_hoje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_emails(update, context)


@only_authorized
async def cmd_semana(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('🔍 Emails da semana (7 dias)...')
    buckets, total, spam, err = fetch_emails(hours=168, limit=200)
    if err:
        await update.message.reply_text(f'❌ Erro: {err}')
        return
    await update.message.reply_text(build_message(buckets, f'📬 Semana — {total} total, {spam} propagandas'))


@only_authorized
async def cmd_urgente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('🔍 Buscando urgentes...')
    buckets, total, spam, err = fetch_emails(hours=None, only_cat='URGENTE', limit=200)
    if err:
        await update.message.reply_text(f'❌ Erro: {err}')
        return
    items = buckets.get('URGENTE', [])
    if not items:
        await update.message.reply_text('✅ Nenhum urgente.')
        return
    msg = f'🔴 URGENTE ({len(items)})\n\n' + '\n'.join(items[:30])
    await update.message.reply_text(msg)
    if len(items) > 30:
        await update.message.reply_text(f'... e mais {len(items)-30} emails urgentes.')


@only_authorized
async def cmd_vendas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('🔍 Buscando vendas...')
    buckets, total, spam, err = fetch_emails(hours=None, only_cat='VENDAS', limit=200)
    if err:
        await update.message.reply_text(f'❌ Erro: {err}')
        return
    items = buckets.get('VENDAS', [])
    msg = (f'💰 VENDAS ({len(items)})\n\n' + '\n'.join(items[:30])) if items else '📭 Nenhuma venda.'
    await update.message.reply_text(msg)


@only_authorized
async def cmd_profissional(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buckets, total, spam, err = fetch_emails(hours=None, only_cat='PROFISSIONAL', limit=100)
    if err:
        await update.message.reply_text(f'❌ Erro: {err}')
        return
    items = buckets.get('PROFISSIONAL', [])
    msg = (f'👨‍💼 PROFISSIONAL ({len(items)})\n\n' + '\n'.join(items[:30])) if items else '📭 Nenhum.'
    await update.message.reply_text(msg)


@only_authorized
async def cmd_financeiro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buckets, total, spam, err = fetch_emails(hours=None, only_cat='FINANCEIRO', limit=100)
    if err:
        await update.message.reply_text(f'❌ Erro: {err}')
        return
    items = buckets.get('FINANCEIRO', [])
    msg = (f'💳 FINANCEIRO ({len(items)})\n\n' + '\n'.join(items[:30])) if items else '📭 Nenhum email financeiro.'
    await update.message.reply_text(msg)


@only_authorized
async def cmd_pessoal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buckets, total, spam, err = fetch_emails(hours=None, only_cat='PESSOAL', limit=100)
    if err:
        await update.message.reply_text(f'❌ Erro: {err}')
        return
    items = buckets.get('PESSOAL', [])
    msg = (f'👦 PESSOAL ({len(items)})\n\n' + '\n'.join(items[:30])) if items else '📭 Nenhum email pessoal.'
    await update.message.reply_text(msg)


@only_authorized
async def cmd_marcar_lido(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('⏳ Marcando TODOS como lidos...')
    total = mark_as_read(hours=None)
    if total == -1:
        await update.message.reply_text('❌ Erro ao marcar emails.')
    elif total == 0:
        await update.message.reply_text('📭 Nenhum email não lido.')
    else:
        await update.message.reply_text(f'✅ {total} email(s) marcados como lidos!')


@only_authorized
async def cmd_analisar_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Busca email por nome e retorna análise + plano de ação."""
    text = update.message.text.strip()
    # Extrai o nome após palavras-chave
    for kw in ['email do', 'email da', 'email de', 'analisar email do', 'analisar email da',
                'analisar email de', 'plano de acao', 'plano de ação', 'o que fazer com']:
        if kw in text.lower():
            name_query = text.lower().replace(kw, '').strip().title()
            break
    else:
        name_query = text.strip()

    if not name_query or len(name_query) < 2:
        await update.message.reply_text('❓ Qual o nome? Ex: "email da Raquel"')
        return

    await update.message.reply_text(f'🔍 Buscando email de {name_query}...')
    email_data = search_email_by_name(name_query)

    if not email_data:
        await update.message.reply_text(f'📭 Nenhum email encontrado de "{name_query}".')
        return

    await update.message.reply_text(
        f'📧 Encontrado!\nDe: {email_data["sender"]}\nAssunto: {email_data["subject"]}\n\n⏳ Analisando com IA...'
    )
    analysis = analyze_with_claude(email_data)
    await update.message.reply_text(analysis)


@only_authorized
async def cmd_gerar_parecer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gera parecer preliminar com base no último email de consulta pendente."""
    if not _parecer_pending:
        await update.message.reply_text('📭 Nenhuma solicitação de parecer pendente.\n\nO bot detecta automaticamente emails pedindo orientação/análise tributária.')
        return

    uid, email_data = list(_parecer_pending.items())[-1]
    empresa = email_data.get('empresa', 'Cliente')
    cnpj = email_data.get('cnpj', '')

    await update.message.reply_text(
        f'⏳ Gerando parecer preliminar...\n'
        f'🏢 Empresa: {empresa}\n'
        f'📋 CNPJ: {cnpj}\n\n'
        f'Aguarde até 30 segundos.'
    )

    parecer = generate_parecer_claude(email_data)
    if not parecer:
        await update.message.reply_text('❌ Erro ao gerar parecer. Verifique a variável ANTHROPIC_API_KEY.')
        return

    _parecer_pending.pop(uid, None)

    header = (
        f'📋 PARECER TÉCNICO PRELIMINAR\n'
        f'🏢 {empresa}'
        + (f' | CNPJ: {cnpj}' if cnpj else '') +
        f'\n⚠️ RASCUNHO — revisar antes de entregar ao cliente\n'
        f'{"─" * 40}\n\n'
    )
    full_text = header + parecer

    # Envia em partes no Telegram
    chunks = [full_text[i:i+4000] for i in range(0, len(full_text), 4000)]
    for chunk in chunks:
        await update.message.reply_text(chunk)

    # Tenta salvar no Google Drive
    link = save_to_drive(full_text, empresa, cnpj)
    if link:
        await update.message.reply_text(f'✅ Salvo no Google Drive:\n{link}')
    else:
        # Envia como arquivo .txt no Telegram
        import io
        from telegram import InputFile
        ts = datetime.now().strftime('%Y%m%d_%H%M')
        empresa_clean = re.sub(r'[^\w\s\-]', '', empresa).strip()[:40]
        filename = f'PARECER_{empresa_clean}_{ts}.txt'
        file_bytes = io.BytesIO(full_text.encode('utf-8'))
        await context.bot.send_document(
            chat_id=CHAT_ID,
            document=InputFile(file_bytes, filename=filename),
            caption=f'📄 {filename}'
        )


@only_authorized
async def cmd_pessoal_gmail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lê somente a conta pessoal cnp.marcoslima@gmail.com"""
    pessoal = next((a for a in ACCOUNTS if 'cnp' in a['user']), None)
    if not pessoal:
        await update.message.reply_text('❌ Conta pessoal não configurada.')
        return
    await update.message.reply_text(f'🔍 Lendo {pessoal["user"]}...')
    try:
        mail = get_imap(pessoal['user'], pessoal['password'], readonly=True)
        ids = search_unseen(mail, hours=None)
        total = len(ids)
        buckets = {k: [] for k in ICONS}
        spam_count = 0
        for eid, sender, subject in fetch_headers(mail, ids, limit=100):
            if is_spam(sender, subject):
                spam_count += 1
                continue
            cat = classify(sender, subject)
            name = sender.split('<')[0].strip().strip('"') or sender
            name = name[:35] + '...' if len(name) > 35 else name
            sub = subject[:55] + '...' if len(subject) > 55 else subject
            buckets[cat].append(f'• {sub}\n  ↳ {name}')
        mail.logout()
        msg = build_message(buckets, f'👤 Pessoal ({pessoal["user"]}) — {total} não lidos, {spam_count} propagandas')
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f'❌ Erro: {e}')


@only_authorized
async def cmd_varredura(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('🔍 Varrendo caixa de entrada...')
    try:
        total = 0
        encontrados = []
        for acc in ACCOUNTS:
            mail = get_imap(acc['user'], acc['password'], readonly=True)
            ids = search_unseen(mail, hours=None)
            total += len(ids)
            for eid, sender, subject in fetch_headers(mail, ids, limit=500):
                kw = match_alert(sender, subject)
                if kw:
                    name = sender.split('<')[0].strip().strip('"') or sender
                    name = name[:35] + '...' if len(name) > 35 else name
                    sub = subject[:55] + '...' if len(subject) > 55 else subject
                    encontrados.append((kw, sub, name, acc['label']))
            mail.logout()

        if not encontrados:
            await update.message.reply_text(
                f'✅ Varredura concluída — {total} emails analisados\n'
                f'Nenhum email com palavras-chave críticas encontrado.'
            )
            return

        lines = [f'🚨 VARREDURA — {len(encontrados)} alertas em {total} emails\n']
        for kw, sub, name, label in encontrados[:30]:
            lines.append(f'⚡ [{kw.upper()}] {label}\n• {sub}\n  ↳ {name}\n')
        msg = '\n'.join(lines)
        await update.message.reply_text(msg)
        if len(encontrados) > 30:
            await update.message.reply_text(f'... e mais {len(encontrados)-30} emails com alertas.')
    except Exception as e:
        await update.message.reply_text(f'❌ Erro na varredura: {e}')


@only_authorized
async def cmd_alertas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kws = '\n'.join(f'• {k}' for k in sorted(ALERT_KEYWORDS))
    await update.message.reply_text(f'🚨 Palavras-chave monitoradas ({len(ALERT_KEYWORDS)}):\n\n{kws}')


@only_authorized
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        '🤖 Fale naturalmente ou use comandos:\n\n'
        '📊 "ler os 1000 emails" — resumo geral\n'
        '☀️ "o que chegou hoje?" — últimas 24h\n'
        '📅 "e essa semana?" — últimos 7 dias\n'
        '🔴 "tem algo urgente?" — urgentes\n'
        '💰 "quanto vendi?" — vendas Eduzz/Hotmart\n'
        '👨‍💼 "ver profissional" — jurídico/tributário\n'
        '💳 "ver financeiro" — XP, Pix, banco\n'
        '👦 "email do davi?" — pessoal/escola\n'
        '🚨 "ver alertas" — palavras monitoradas\n'
        '✅ "zera a caixa" — marca tudo como lido\n\n'
        '🔔 Monitoramento automático ativo a cada 10 min!\n'
        'Alertas disparam para: clientes, SPED, PGFN, etc.\n\n'
        '📝 PARECERES:\n'
        'Quando chegar email pedindo orientação/análise tributária,\n'
        'o bot avisa automaticamente. Responda "parecer" para gerar\n'
        'o parecer preliminar com as 10 seções da Compliance.'
    )


@only_authorized
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower().strip()

    if any(w in text for w in [
        'todos', 'tudo', '1000', '1.000', 'pendentes', 'quantos', 'fila',
        'geral', 'não lidos', 'nao lidos', 'caixa toda', 'caixa inteira',
        'ler tudo', 'ver tudo', 'listar tudo', 'quanto tem', 'quantos tem',
        'overview', 'visao geral', 'visão geral',
    ]):
        await cmd_todos(update, context)

    elif any(w in text for w in [
        'hoje', 'do dia', 'chegou hoje', 'recebi hoje',
        'ultimas 24', 'últimas 24', 'ultimo dia', 'último dia',
    ]):
        await cmd_hoje(update, context)

    elif any(w in text for w in [
        'semana', 'essa semana', 'esta semana', 'ultimos 7', 'últimos 7',
    ]):
        await cmd_semana(update, context)

    elif any(w in text for w in [
        'urgente', 'urgentes', 'importante', 'importantes', 'critico',
        'crítico', 'atenção', 'atencao', 'resolver', 'pendencia',
        'pendência', 'prazo', 'vencer', 'vencendo', 'gestta',
        'certificado', 'regularize', 'pgfn',
    ]):
        await cmd_urgente(update, context)

    elif any(w in text for w in [
        'venda', 'vendas', 'vendi', 'vendeu', 'vendemos',
        'hotmart', 'eduzz', 'faturamento', 'novo aluno', 'nova venda', 'quanto vendi',
    ]):
        await cmd_vendas(update, context)

    elif any(w in text for w in [
        'profissional', 'juridico', 'tributario', 'conjur', 'legisweb',
        'jurídico', 'tributário', 'reforma tributaria', 'boletim', 'legislacao',
    ]):
        await cmd_profissional(update, context)

    elif any(w in text for w in [
        'financeiro', 'financeira', 'xp', 'xpi', 'pix', 'banco',
        'extrato', 'transferencia', 'investimento',
    ]):
        await cmd_financeiro(update, context)

    elif any(w in text for w in [
        'pessoal', 'davi', 'escola', 'familia', 'família', 'colmaster', 'nota', 'filho',
    ]):
        await cmd_pessoal(update, context)

    elif any(w in text for w in [
        'parecer', 'gerar parecer', 'quero parecer', 'faz o parecer',
        'elaborar parecer', 'monta o parecer', 'gera o parecer',
    ]):
        await cmd_gerar_parecer(update, context)

    elif any(w in text for w in [
        'email do', 'email da', 'email de', 'analisar email',
        'plano de acao', 'plano de ação', 'o que fazer com',
        'o que preciso fazer', 'analisa email',
    ]):
        await cmd_analisar_email(update, context)

    elif any(w in text for w in [
        'email pessoal', 'emails pessoais', 'cnp', 'gmail pessoal',
        'meu pessoal', 'conta pessoal', 'ler pessoal',
    ]):
        await cmd_pessoal_gmail(update, context)

    elif any(w in text for w in [
        'varredura', 'varrer', 'buscar alertas', 'busca agora', 'checar agora',
        'verificar agora', 'procurar clientes', 'scan',
    ]):
        await cmd_varredura(update, context)

    elif any(w in text for w in [
        'alerta', 'alertas', 'monitorando', 'palavras', 'palavras-chave', 'keywords',
    ]):
        await cmd_alertas(update, context)

    elif any(w in text for w in [
        'marcar', 'lido', 'lidos', 'arquivar', 'limpar', 'zerar',
        'zera', 'limpa', 'marca lido', 'marca todos', 'marcar como lido',
    ]):
        await cmd_marcar_lido(update, context)

    elif any(w in text for w in [
        'email', 'emails', 'ler', 'leia', 'resumo', 'caixa', 'inbox',
        'mensagem', 'mensagens', 'me mostra', 'o que tem',
    ]):
        await cmd_todos(update, context)

    elif any(w in text for w in ['ajuda', 'help', 'comandos', 'menu']):
        await cmd_help(update, context)

    else:
        await update.message.reply_text(
            'Não entendi 😅\n\n'
            'Exemplos:\n'
            '• "ler os 1000 emails"\n'
            '• "tem algo urgente?"\n'
            '• "quanto vendi essa semana?"\n'
            '• "o que chegou hoje?"\n'
            '• "zera a caixa"\n'
            '• "ver alertas"\n\n'
            'Digite ajuda para tudo.'
        )


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Monitoramento automático a cada 10 minutos
    app.job_queue.run_repeating(check_alerts, interval=600, first=30)

    app.add_handler(CommandHandler('start', cmd_todos))
    app.add_handler(CommandHandler('todos', cmd_todos))
    app.add_handler(CommandHandler('emails', cmd_emails))
    app.add_handler(CommandHandler('hoje', cmd_hoje))
    app.add_handler(CommandHandler('semana', cmd_semana))
    app.add_handler(CommandHandler('urgente', cmd_urgente))
    app.add_handler(CommandHandler('vendas', cmd_vendas))
    app.add_handler(CommandHandler('profissional', cmd_profissional))
    app.add_handler(CommandHandler('financeiro', cmd_financeiro))
    app.add_handler(CommandHandler('pessoal', cmd_pessoal))
    app.add_handler(CommandHandler('analisar', cmd_analisar_email))
    app.add_handler(CommandHandler('parecer', cmd_gerar_parecer))
    app.add_handler(CommandHandler('cnp', cmd_pessoal_gmail))
    app.add_handler(CommandHandler('varredura', cmd_varredura))
    app.add_handler(CommandHandler('alertas', cmd_alertas))
    app.add_handler(CommandHandler('marcar', cmd_marcar_lido))
    app.add_handler(CommandHandler('ajuda', cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print('Bot rodando com monitoramento a cada 10 minutos...')
    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
