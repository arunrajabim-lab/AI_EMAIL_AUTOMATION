import os
import json
import time
import base64
import mimetypes
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from libtime import timezone_for_city


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

MAILS_DIR = os.path.join(BASE_DIR, "MAILS")
PENDING_DIR = os.path.join(MAILS_DIR, "pending")
COMPLETED_DIR = os.path.join(MAILS_DIR, "completed")

TEMPLATES_DIR = os.path.join(BASE_DIR, "TEMPLATES")
DATA_DIR = os.path.join(BASE_DIR, "DATA")
LOG_DIR = os.path.join(BASE_DIR, "LOGS")

SENT_RECORDS_FILE = os.path.join(DATA_DIR, "sent_records.json")
SCHEDULE_FILE = os.path.join(DATA_DIR, "scheduled_records.json")
DAILY_MAILS_FILE = os.path.join(PENDING_DIR, "daily_mails.txt")

TEST_MODE = False

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify"
]


def setup_folders():
    folders = [
        PENDING_DIR,
        COMPLETED_DIR,
        DATA_DIR,
        LOG_DIR,
        os.path.join(TEMPLATES_DIR, "job"),
        os.path.join(TEMPLATES_DIR, "freelance"),
        os.path.join(TEMPLATES_DIR, "remote"),
    ]

    for folder in folders:
        os.makedirs(folder, exist_ok=True)


def log(message):
    os.makedirs(LOG_DIR, exist_ok=True)

    filename = datetime.now().strftime("%Y-%m-%d") + ".log"
    path = os.path.join(LOG_DIR, filename)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    text = f"[{timestamp}] {message}"

    print(text)

    with open(path, "a", encoding="utf-8") as f:
        f.write(text + "\n")


def load_json(path, default):
    if not os.path.exists(path):
        return default

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"JSON READ ERROR | {path} | {e}")
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)

    temp = path + ".tmp"

    with open(temp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    os.replace(temp, path)


# ============================================================
# GMAIL
# ============================================================

def get_gmail_service():

    token_data = os.environ.get("GMAIL_TOKEN")

    if not token_data:
        raise RuntimeError(
            "GMAIL_TOKEN GitHub Secret not found."
        )

    try:
        token_info = json.loads(token_data)
    except Exception:
        raise RuntimeError(
            "GMAIL_TOKEN is not valid JSON."
        )

    credentials = Credentials.from_authorized_user_info(
        token_info,
        SCOPES
    )

    return build(
        "gmail",
        "v1",
        credentials=credentials
    )


# ============================================================
# DAILY MAILS
# ============================================================

def parse_daily_mails():

    if not os.path.exists(DAILY_MAILS_FILE):
        log("daily_mails.txt not found.")
        return []

    with open(
        DAILY_MAILS_FILE,
        "r",
        encoding="utf-8"
    ) as f:
        content = f.read()

    blocks = content.split("---MAIL---")

    mails = []

    for block in blocks:

        block = block.strip()

        if not block:
            continue

        to = ""
        location = ""
        mail_type = ""

        for line in block.splitlines():

            line = line.strip()

            if line.upper().startswith("TO:"):
                to = line[3:].strip()

            elif line.upper().startswith("LOCATION:"):
                location = line[9:].strip()

            elif line.upper().startswith("TYPE:"):
                mail_type = line[5:].strip().lower()

        if not to:
            log("MAIL SKIPPED | TO missing")
            continue

        if not location:
            log(f"MAIL SKIPPED | {to} | LOCATION missing")
            continue

        if mail_type not in (
            "job",
            "freelance",
            "remote"
        ):
            log(
                f"MAIL SKIPPED | {to} | "
                f"Invalid TYPE: {mail_type}"
            )
            continue

        mails.append({
            "to": to.lower(),
            "location": location,
            "type": mail_type
        })

    return mails


# ============================================================
# TIMEZONE
# ============================================================

def calculate_schedule(location):

    tz_name = timezone_for_city(location)

    if not tz_name:
        return None, None

    try:
        local_zone = ZoneInfo(tz_name)
    except Exception:
        return None, None

    now_local = datetime.now(local_zone)

    next_day = now_local.date() + timedelta(days=1)

    target_local = datetime(
        next_day.year,
        next_day.month,
        next_day.day,
        10,
        20,
        0,
        tzinfo=local_zone
    )

    target_utc = target_local.astimezone(timezone.utc)

    return target_utc, tz_name


# ============================================================
# TEMPLATE
# ============================================================

def load_template(mail_type):

    file_path = os.path.join(
        TEMPLATES_DIR,
        mail_type,
        "mail.txt"
    )

    if not os.path.exists(file_path):
        raise FileNotFoundError(
            f"Missing template: {file_path}"
        )

    with open(
        file_path,
        "r",
        encoding="utf-8"
    ) as f:
        content = f.read()

    subject = ""
    body_lines = []
    body_started = False

    for line in content.splitlines():

        if line.upper().startswith("SUBJECT:"):
            subject = line[8:].strip()

        elif line.upper().strip() == "BODY:":
            body_started = True

        elif body_started:
            body_lines.append(line)

    body = "\n".join(body_lines).strip()

    if not subject:
        raise ValueError("SUBJECT missing")

    if not body:
        raise ValueError("BODY missing")

    return subject, body


# ============================================================
# ATTACHMENTS
# ============================================================

def get_attachments(mail_type):

    folder = os.path.join(
        TEMPLATES_DIR,
        mail_type
    )

    if not os.path.exists(folder):
        return []

    files = []

    for filename in os.listdir(folder):

        if filename.lower().endswith(".pdf"):

            path = os.path.join(
                folder,
                filename
            )

            if os.path.isfile(path):
                files.append(path)

    return sorted(files)


# ============================================================
# MESSAGE
# ============================================================

def create_message(
    to,
    subject,
    body,
    attachments
):

    message = EmailMessage()

    message["To"] = to
    message["Subject"] = subject

    message.set_content(body)

    for path in attachments:

        mime_type, _ = mimetypes.guess_type(path)

        if not mime_type:
            mime_type = "application/pdf"

        maintype, subtype = mime_type.split("/", 1)

        with open(path, "rb") as f:
            data = f.read()

        message.add_attachment(
            data,
            maintype=maintype,
            subtype=subtype,
            filename=os.path.basename(path)
        )

    encoded = base64.urlsafe_b64encode(
        message.as_bytes()
    ).decode()

    return {
        "raw": encoded
    }


# ============================================================
# SCHEDULE
# ============================================================

def schedule_mail(
    mail,
    scheduled_records,
    sent_records
):

    email = mail["to"]

    if email in sent_records:
        log(f"DUPLICATE BLOCKED | {email}")
        return

    if email in scheduled_records:
        return

    target_utc, tz_name = calculate_schedule(
        mail["location"]
    )

    if not target_utc:
        log(
            f"TIMEZONE ERROR | "
            f"{email} | {mail['location']}"
        )
        return

    try:
        subject, body = load_template(
            mail["type"]
        )

        attachments = get_attachments(
            mail["type"]
        )

    except Exception as e:
        log(
            f"TEMPLATE ERROR | "
            f"{email} | {e}"
        )
        return

    scheduled_records[email] = {

        "email": email,

        "location": mail["location"],

        "timezone": tz_name,

        "type": mail["type"],

        "subject": subject,

        "send_utc": target_utc.isoformat(),

        "created_at": datetime.now(
            timezone.utc
        ).isoformat()
    }

    save_json(
        SCHEDULE_FILE,
        scheduled_records
    )

    log(
        f"SCHEDULED | "
        f"{email} | "
        f"{mail['location']} | "
        f"{mail['type']} | "
        f"PDFs: {len(attachments)}"
    )


# ============================================================
# SEND DUE
# ============================================================

def send_due_emails(
    scheduled_records,
    sent_records,
    service
):

    now_utc = datetime.now(timezone.utc)

    for email in list(scheduled_records.keys()):

        record = scheduled_records[email]

        try:
            send_time = datetime.fromisoformat(
                record["send_utc"]
            )
        except Exception as e:
            log(
                f"INVALID SCHEDULE | "
                f"{email} | {e}"
            )
            continue

        if now_utc < send_time:
            continue

        if email in sent_records:
            log(
                f"DUPLICATE BLOCKED AT SEND | "
                f"{email}"
            )

            del scheduled_records[email]

            save_json(
                SCHEDULE_FILE,
                scheduled_records
            )

            continue

        try:

            subject, body = load_template(
                record["type"]
            )

            attachments = get_attachments(
                record["type"]
            )

            gmail_message = create_message(
                email,
                subject,
                body,
                attachments
            )

            result = service.users().messages().send(
                userId="me",
                body=gmail_message
            ).execute()

            message_id = result.get("id", "")

            sent_records[email] = {

                "email": email,

                "location": record["location"],

                "timezone": record["timezone"],

                "type": record["type"],

                "subject": subject,

                "sent_at": datetime.now(
                    timezone.utc
                ).isoformat(),

                "gmail_message_id": message_id,

                "attachments": [
                    os.path.basename(x)
                    for x in attachments
                ]
            }

            save_json(
                SENT_RECORDS_FILE,
                sent_records
            )

            del scheduled_records[email]

            save_json(
                SCHEDULE_FILE,
                scheduled_records
            )

            log(
                f"EMAIL SENT | "
                f"{email} | "
                f"Gmail ID: {message_id}"
            )

        except HttpError as e:

            log(
                f"GMAIL ERROR | "
                f"{email} | {e}"
            )

        except Exception as e:

            log(
                f"SEND ERROR | "
                f"{email} | {e}"
            )


# ============================================================
# MAIN
# ============================================================

def main():

    setup_folders()

    scheduled_records = load_json(
        SCHEDULE_FILE,
        {}
    )

    sent_records = load_json(
        SENT_RECORDS_FILE,
        {}
    )

    service = get_gmail_service()

    log("=" * 60)
    log("AI EMAIL AUTOMATION STARTED")
    log("GitHub Actions single-run mode")
    log("=" * 60)

    mails = parse_daily_mails()

    for mail in mails:

        schedule_mail(
            mail,
            scheduled_records,
            sent_records
        )

    send_due_emails(
        scheduled_records,
        sent_records,
        service
    )

    log("RUN COMPLETE")


if __name__ == "__main__":
    main()
