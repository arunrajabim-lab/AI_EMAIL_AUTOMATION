import os
import json
import base64
import mimetypes
import shutil
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from zoneinfo import ZoneInfo

import requests

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from googleapiclient.errors import HttpError

from libtime import timezone_for_city


# ============================================================
# CONFIG
# ============================================================

DRIVE_ROOT_FOLDER_ID = "1Ob0xiMyoV4iWR3rnppjVkWUlqZ705oFx"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

MAILS_DIR = os.path.join(BASE_DIR, "MAILS")
PENDING_DIR = os.path.join(MAILS_DIR, "pending")
COMPLETED_DIR = os.path.join(MAILS_DIR, "completed")

TEMPLATES_DIR = os.path.join(BASE_DIR, "TEMPLATES")

DATA_DIR = os.path.join(BASE_DIR, "DATA")
LOG_DIR = os.path.join(BASE_DIR, "LOGS")

DAILY_MAILS_FILE = os.path.join(
    PENDING_DIR,
    "daily_mails.txt"
)

SENT_RECORDS_FILE = os.path.join(
    DATA_DIR,
    "sent_records.json"
)

SCHEDULE_FILE = os.path.join(
    DATA_DIR,
    "scheduled_records.json"
)

TEST_MODE = False

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/drive",
]

GEMINI_MODEL = "gemini-2.5-flash"


# ============================================================
# FOLDERS
# ============================================================

def setup_folders():

    folders = [
        MAILS_DIR,
        PENDING_DIR,
        COMPLETED_DIR,
        TEMPLATES_DIR,
        DATA_DIR,
        LOG_DIR,

        os.path.join(TEMPLATES_DIR, "job"),
        os.path.join(TEMPLATES_DIR, "freelance"),
        os.path.join(TEMPLATES_DIR, "remote"),
    ]

    for folder in folders:
        os.makedirs(folder, exist_ok=True)


# ============================================================
# LOG
# ============================================================

def log(message):

    os.makedirs(LOG_DIR, exist_ok=True)

    filename = datetime.now().strftime("%Y-%m-%d") + ".log"

    path = os.path.join(
        LOG_DIR,
        filename
    )

    timestamp = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    text = f"[{timestamp}] {message}"

    print(text)

    with open(
        path,
        "a",
        encoding="utf-8"
    ) as f:
        f.write(text + "\n")


# ============================================================
# JSON
# ============================================================

def load_json(path, default):

    if not os.path.exists(path):
        return default

    try:

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception as e:

        log(
            f"JSON READ ERROR | {path} | {e}"
        )

        return default


def save_json(path, data):

    os.makedirs(
        os.path.dirname(path),
        exist_ok=True
    )

    temp = path + ".tmp"

    with open(
        temp,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            indent=2,
            ensure_ascii=False
        )

    os.replace(
        temp,
        path
    )


# ============================================================
# GOOGLE AUTH
# ============================================================

def get_credentials():

    token_data = os.environ.get(
        "GMAIL_TOKEN"
    )

    if not token_data:

        raise RuntimeError(
            "GMAIL_TOKEN GitHub Secret not found."
        )

    try:

        token_info = json.loads(
            token_data
        )

    except Exception:

        raise RuntimeError(
            "GMAIL_TOKEN is not valid JSON."
        )

    credentials = Credentials.from_authorized_user_info(
        token_info,
        SCOPES
    )

    return credentials


# ============================================================
# GMAIL SERVICE
# ============================================================

def get_gmail_service(credentials):

    return build(
        "gmail",
        "v1",
        credentials=credentials,
        cache_discovery=False
    )


# ============================================================
# DRIVE SERVICE
# ============================================================

def get_drive_service(credentials):

    return build(
        "drive",
        "v3",
        credentials=credentials,
        cache_discovery=False
    )


# ============================================================
# DRIVE HELPERS
# ============================================================

def drive_list_children(
    service,
    folder_id
):

    results = []

    page_token = None

    while True:

        response = service.files().list(
            q=(
                f"'{folder_id}' in parents "
                f"and trashed = false"
            ),
            spaces="drive",
            fields=(
                "nextPageToken,"
                "files(id,name,mimeType,size)"
            ),
            pageToken=page_token,
            pageSize=1000
        ).execute()

        results.extend(
            response.get("files", [])
        )

        page_token = response.get(
            "nextPageToken"
        )

        if not page_token:
            break

    return results


def download_drive_file(
    service,
    file_id,
    destination
):

    os.makedirs(
        os.path.dirname(destination),
        exist_ok=True
    )

    request = service.files().get_media(
        fileId=file_id
    )

    with open(
        destination,
        "wb"
    ) as f:

        downloader = MediaIoBaseDownload(
            f,
            request
        )

        done = False

        while not done:

            _, done = downloader.next_chunk()


def download_drive_tree(
    service,
    drive_folder_id,
    local_folder
):

    os.makedirs(
        local_folder,
        exist_ok=True
    )

    children = drive_list_children(
        service,
        drive_folder_id
    )

    for item in children:

        name = item["name"]
        item_id = item["id"]
        mime_type = item["mimeType"]

        local_path = os.path.join(
            local_folder,
            name
        )

        if mime_type == "application/vnd.google-apps.folder":

            download_drive_tree(
                service,
                item_id,
                local_path
            )

        elif mime_type.startswith(
            "application/vnd.google-apps."
        ):

            log(
                f"SKIP GOOGLE DOCUMENT | {name}"
            )

        else:

            try:

                download_drive_file(
                    service,
                    item_id,
                    local_path
                )

            except Exception as e:

                log(
                    f"DRIVE DOWNLOAD ERROR | "
                    f"{name} | {e}"
                )


def find_drive_file(
    service,
    folder_id,
    filename
):

    children = drive_list_children(
        service,
        folder_id
    )

    for item in children:

        if (
            item["name"] == filename
            and item["mimeType"]
            != "application/vnd.google-apps.folder"
        ):

            return item

    return None


def find_drive_folder(
    service,
    parent_id,
    folder_name
):

    children = drive_list_children(
        service,
        parent_id
    )

    for item in children:

        if (
            item["name"] == folder_name
            and item["mimeType"]
            == "application/vnd.google-apps.folder"
        ):

            return item

    return None


def ensure_drive_folder(
    service,
    parent_id,
    folder_name
):

    existing = find_drive_folder(
        service,
        parent_id,
        folder_name
    )

    if existing:
        return existing["id"]

    metadata = {
        "name": folder_name,
        "mimeType": (
            "application/vnd.google-apps.folder"
        ),
        "parents": [parent_id]
    }

    result = service.files().create(
        body=metadata,
        fields="id"
    ).execute()

    return result["id"]


def upload_or_update_file(
    service,
    local_path,
    drive_folder_id
):

    filename = os.path.basename(
        local_path
    )

    existing = find_drive_file(
        service,
        drive_folder_id,
        filename
    )

    mime_type, _ = mimetypes.guess_type(
        local_path
    )

    if not mime_type:
        mime_type = "application/octet-stream"

    media = MediaFileUpload(
        local_path,
        mimetype=mime_type,
        resumable=True
    )

    if existing:

        service.files().update(
            fileId=existing["id"],
            media_body=media
        ).execute()

        return existing["id"]

    metadata = {
        "name": filename,
        "parents": [drive_folder_id]
    }

    result = service.files().create(
        body=metadata,
        media_body=media,
        fields="id"
    ).execute()

    return result["id"]


def upload_folder_to_drive(
    service,
    local_folder,
    drive_folder_id
):

    if not os.path.exists(local_folder):
        return

    for name in os.listdir(local_folder):

        local_path = os.path.join(
            local_folder,
            name
        )

        if os.path.isdir(local_path):

            child_drive_folder = ensure_drive_folder(
                service,
                drive_folder_id,
                name
            )

            upload_folder_to_drive(
                service,
                local_path,
                child_drive_folder
            )

        else:

            try:

                upload_or_update_file(
                    service,
                    local_path,
                    drive_folder_id
                )

            except Exception as e:

                log(
                    f"DRIVE UPLOAD ERROR | "
                    f"{local_path} | {e}"
                )


# ============================================================
# SYNC DRIVE → LOCAL
# ============================================================

def sync_from_drive(
    drive_service
):

    log(
        "SYNC START | Google Drive → GitHub Runner"
    )

    # Remove old temporary content.
    # Keep repository Python files.
    for folder in [
        MAILS_DIR,
        TEMPLATES_DIR,
        DATA_DIR,
        LOG_DIR
    ]:

        if os.path.exists(folder):

            shutil.rmtree(
                folder
            )

    setup_folders()

    download_drive_tree(
        drive_service,
        DRIVE_ROOT_FOLDER_ID,
        BASE_DIR
    )

    log(
        "SYNC COMPLETE"
    )


# ============================================================
# SYNC LOCAL DATA BACK TO DRIVE
# ============================================================

def sync_data_to_drive(
    drive_service
):

    try:

        data_folder = ensure_drive_folder(
            drive_service,
            DRIVE_ROOT_FOLDER_ID,
            "DATA"
        )

        upload_folder_to_drive(
            drive_service,
            DATA_DIR,
            data_folder
        )

        logs_folder = ensure_drive_folder(
            drive_service,
            DRIVE_ROOT_FOLDER_ID,
            "LOGS"
        )

        upload_folder_to_drive(
            drive_service,
            LOG_DIR,
            logs_folder
        )

        log(
            "DRIVE DATA SYNC COMPLETE"
        )

    except Exception as e:

        log(
            f"DRIVE DATA SYNC ERROR | {e}"
        )


# ============================================================
# DAILY MAILS
# ============================================================

def parse_daily_mails():

    if not os.path.exists(
        DAILY_MAILS_FILE
    ):

        log(
            "daily_mails.txt NOT FOUND"
        )

        return []

    with open(
        DAILY_MAILS_FILE,
        "r",
        encoding="utf-8"
    ) as f:

        content = f.read()

    blocks = content.split(
        "---MAIL---"
    )

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

            elif line.upper().startswith(
                "LOCATION:"
            ):

                location = line[9:].strip()

            elif line.upper().startswith(
                "TYPE:"
            ):

                mail_type = line[5:].strip().lower()

        if not to:
            log(
                "MAIL SKIPPED | TO missing"
            )
            continue

        if not location:
            log(
                f"MAIL SKIPPED | "
                f"{to} | LOCATION missing"
            )
            continue

        if mail_type not in (
            "job",
            "freelance",
            "remote"
        ):

            log(
                f"MAIL SKIPPED | "
                f"{to} | Invalid TYPE"
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

def calculate_schedule(
    location
):

    tz_name = timezone_for_city(
        location
    )

    if not tz_name:
        return None, None

    try:

        local_zone = ZoneInfo(
            tz_name
        )

    except Exception:

        return None, None

    now_local = datetime.now(
        local_zone
    )

    next_day = (
        now_local.date()
        + timedelta(days=1)
    )

    target_local = datetime(
        next_day.year,
        next_day.month,
        next_day.day,
        10,
        20,
        0,
        tzinfo=local_zone
    )

    target_utc = target_local.astimezone(
        timezone.utc
    )

    return target_utc, tz_name


# ============================================================
# TEMPLATE
# ============================================================

def load_template(
    mail_type
):

    file_path = os.path.join(
        TEMPLATES_DIR,
        mail_type,
        "mail.txt"
    )

    if not os.path.exists(
        file_path
    ):

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

        if line.upper().startswith(
            "SUBJECT:"
        ):

            subject = line[8:].strip()

        elif line.upper().strip() == "BODY:":

            body_started = True

        elif body_started:

            body_lines.append(line)

    body = "\n".join(
        body_lines
    ).strip()

    if not subject:
        raise ValueError(
            "SUBJECT missing"
        )

    if not body:
        raise ValueError(
            "BODY missing"
        )

    return subject, body


# ============================================================
# ATTACHMENTS
# ============================================================

def get_attachments(
    mail_type
):

    folder = os.path.join(
        TEMPLATES_DIR,
        mail_type
    )

    if not os.path.exists(folder):
        return []

    files = []

    for filename in os.listdir(
        folder
    ):

        if filename.lower().endswith(
            ".pdf"
        ):

            path = os.path.join(
                folder,
                filename
            )

            if os.path.isfile(path):
                files.append(path)

    return sorted(files)


# ============================================================
# GEMINI
# ============================================================

def personalize_with_gemini(
    subject,
    body,
    location,
    mail_type
):

    api_key = os.environ.get(
        "GEMINI_API_KEY"
    )

    if not api_key:
        log(
            "GEMINI_API_KEY not found | "
            "Using original template"
        )

        return subject, body

    prompt = f"""
You are an email personalization assistant.

Personalize this professional job application email.

Location:
{location}

Email type:
{mail_type}

Original subject:
{subject}

Original body:
{body}

Rules:
1. Keep it professional.
2. Keep it concise.
3. Do not invent company names.
4. Do not invent experience.
5. Do not invent qualifications.
6. Keep the sender name Arun.
7. Keep the original meaning.
8. Return ONLY valid JSON.
9. JSON keys must be exactly:
   subject
   body

Example:
{{
  "subject": "Professional subject",
  "body": "Professional email body"
}}
"""

    url = (
        "https://generativelanguage.googleapis.com/"
        "v1beta/models/"
        f"{GEMINI_MODEL}:generateContent"
        f"?key={api_key}"
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": prompt
                    }
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.3
        }
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=60
        )

        response.raise_for_status()

        data = response.json()

        text = (
            data["candidates"][0]
            ["content"]["parts"][0]["text"]
        )

        text = text.strip()

        if text.startswith("```"):
            text = text.replace(
                "```json",
                ""
            ).replace(
                "```",
                ""
            ).strip()

        result = json.loads(text)

        new_subject = result.get(
            "subject",
            subject
        )

        new_body = result.get(
            "body",
            body
        )

        log(
            f"GEMINI PERSONALIZED | "
            f"{location} | {mail_type}"
        )

        return (
            new_subject,
            new_body
        )

    except Exception as e:

        log(
            f"GEMINI ERROR | "
            f"{e} | Using original template"
        )

        return subject, body


# ============================================================
# CREATE GMAIL MESSAGE
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

    message.set_content(
        body
    )

    for path in attachments:

        mime_type, _ = mimetypes.guess_type(
            path
        )

        if not mime_type:
            mime_type = "application/pdf"

        maintype, subtype = mime_type.split(
            "/",
            1
        )

        with open(
            path,
            "rb"
        ) as f:

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

        log(
            f"DUPLICATE BLOCKED | {email}"
        )

        return

    if email in scheduled_records:
        return

    target_utc, tz_name = calculate_schedule(
        mail["location"]
    )

    if not target_utc:

        log(
            f"TIMEZONE ERROR | "
            f"{email} | "
            f"{mail['location']}"
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
        f"{tz_name} | "
        f"10:20 AM LOCAL | "
        f"PDFs: {len(attachments)}"
    )


# ============================================================
# SEND DUE EMAILS
# ============================================================

def send_due_emails(
    scheduled_records,
    sent_records,
    gmail_service
):

    now_utc = datetime.now(
        timezone.utc
    )

    for email in list(
        scheduled_records.keys()
    ):

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

            subject, body = personalize_with_gemini(
                subject,
                body,
                record["location"],
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

            result = (
                gmail_service
                .users()
                .messages()
                .send(
                    userId="me",
                    body=gmail_message
                )
                .execute()
            )

            message_id = result.get(
                "id",
                ""
            )

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

    log("=" * 60)
    log("AI EMAIL AUTOMATION STARTED")
    log("Google Drive + Gemini + Gmail")
    log("=" * 60)

    credentials = get_credentials()

    drive_service = get_drive_service(
        credentials
    )

    gmail_service = get_gmail_service(
        credentials
    )

    # --------------------------------------------------------
    # DOWNLOAD MASTER FOLDER FROM GOOGLE DRIVE
    # --------------------------------------------------------

    sync_from_drive(
        drive_service
    )

    scheduled_records = load_json(
        SCHEDULE_FILE,
        {}
    )

    sent_records = load_json(
        SENT_RECORDS_FILE,
        {}
    )

    # --------------------------------------------------------
    # READ MAIL INPUT
    # --------------------------------------------------------

    mails = parse_daily_mails()

    log(
        f"MAILS FOUND | {len(mails)}"
    )

    # --------------------------------------------------------
    # CREATE SCHEDULES
    # --------------------------------------------------------

    for mail in mails:

        schedule_mail(
            mail,
            scheduled_records,
            sent_records
        )

    # --------------------------------------------------------
    # SEND DUE EMAILS
    # --------------------------------------------------------

    send_due_emails(
        scheduled_records,
        sent_records,
        gmail_service
    )

    # --------------------------------------------------------
    # SAVE DATA BACK TO DRIVE
    # --------------------------------------------------------

    sync_data_to_drive(
        drive_service
    )

    log(
        "RUN COMPLETE"
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
