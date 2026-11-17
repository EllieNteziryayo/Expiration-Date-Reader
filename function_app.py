import os
import json
import uuid
import re
import logging
import traceback

import azure.functions as func
from azure.storage.blob import BlobServiceClient
from azure.identity import DefaultAzureCredential
from azure.ai.documentintelligence import DocumentIntelligenceClient

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff"}
CONTAINER_NAME = "cicc-blobs"


def is_allowed_file(filename: str) -> bool:
    ext = os.path.splitext(filename)[1].lower()
    return ext in ALLOWED_EXTENSIONS


def normalize_text(text: str) -> str:
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def extract_expiration_date(text: str) -> dict:
    text = normalize_text(text)
    lines = [line.strip() for line in text.split("\n") if line.strip()]

    keyword_pattern = r"\b(?:EXP|EXPIRES|EXPIRATION DATE|BEST BEFORE|BEST BY|BEST IF USED BY|USE BY|SELL BY|BB)\b"

    date_patterns = [
        r"([0-9]{4}\s+(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|SEPT|OCT|NOV|DEC)[A-Z]*\s+[0-9]{1,2})",  # 2025 FEB 07
        r"([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})",  # 12/11/26
        r"([0-9]{4}[/-][0-9]{1,2}[/-][0-9]{1,2})",    # 2026-12-11
        r"((?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|SEPT|OCT|NOV|DEC)[A-Z]*\s+[0-9]{1,2},?\s+[0-9]{2,4})",  # MAY 29 2026
        r"([0-9]{1,2}\s+(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|SEPT|OCT|NOV|DEC)[A-Z]*\s+[0-9]{2,4})",   # 11 JUN 2026
        r"((?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|SEPT|OCT|NOV|DEC)[A-Z]*\s+[0-9]{4})",  # AUG 2027
    ]

    # 1. Same line match
    for line in lines:
        if re.search(keyword_pattern, line, re.IGNORECASE):
            for dp in date_patterns:
                match = re.search(dp, line, re.IGNORECASE)
                if match:
                    return {
                        "found": True,
                        "label": "keyword_line",
                        "expiration_date": match.group(1).strip(),
                        "raw_match": line,
                        "method": "keyword_same_line"
                    }

    # 2. Next line match
    for i, line in enumerate(lines):
        if re.search(keyword_pattern, line, re.IGNORECASE):
            for j in range(1, 3):
                if i + j < len(lines):
                    next_line = lines[i + j]
                    for dp in date_patterns:
                        match = re.search(dp, next_line, re.IGNORECASE)
                        if match:
                            return {
                                "found": True,
                                "label": "keyword_next_line",
                                "expiration_date": match.group(1).strip(),
                                "raw_match": f"{line} | {next_line}",
                                "method": "keyword_next_line"
                            }

    # 3. Split keyword across two lines
    for i in range(len(lines) - 1):
        combined = f"{lines[i]} {lines[i + 1]}"

        if re.search(keyword_pattern, combined, re.IGNORECASE):
            for dp in date_patterns:
                match = re.search(dp, combined, re.IGNORECASE)
                if match:
                    return {
                        "found": True,
                        "label": "keyword_split_line",
                        "expiration_date": match.group(1).strip(),
                        "raw_match": combined,
                        "method": "keyword_split_line"
                    }

            if i + 2 < len(lines):
                next_line = lines[i + 2]
                for dp in date_patterns:
                    match = re.search(dp, next_line, re.IGNORECASE)
                    if match:
                        return {
                            "found": True,
                            "label": "keyword_split_line_next_date",
                            "expiration_date": match.group(1).strip(),
                            "raw_match": f"{combined} | {next_line}",
                            "method": "keyword_split_line"
                        }

    return {
        "found": False,
        "label": None,
        "expiration_date": None,
        "raw_match": None,
        "method": "not_found"
    }


@app.route(route="ciccFuncExpDate")
def ciccFuncExpDate(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Processing expiration date request...")

    try:
        file = req.files.get("file")

        if not file:
            return func.HttpResponse(
                json.dumps({"error": "No file uploaded"}),
                status_code=400,
                mimetype="application/json",
                headers={"Access-Control-Allow-Origin": "*"}
            )

        if not is_allowed_file(file.filename):
            return func.HttpResponse(
                json.dumps({"error": "Invalid file type"}),
                status_code=400,
                mimetype="application/json",
                headers={"Access-Control-Allow-Origin": "*"}
            )

        file_bytes = file.read()
        original_name = file.filename
        safe_name = original_name.replace(" ", "_")
        blob_name = f"exp_{uuid.uuid4()}_{safe_name}"

        credential = DefaultAzureCredential()

        blob_service_client = BlobServiceClient(
            account_url=os.environ["STORAGE_BLOB_URL"],
            credential=credential
        )

        blob_client = blob_service_client.get_blob_client(
            container=CONTAINER_NAME,
            blob=blob_name
        )

        blob_client.upload_blob(file_bytes, overwrite=True)

        doc_client = DocumentIntelligenceClient(
            endpoint=os.environ["DOCUMENT_INTELLIGENCE_ENDPOINT"],
            credential=credential
        )

        poller = doc_client.begin_analyze_document(
            model_id="prebuilt-layout",
            body=file_bytes
        )

        result = poller.result()

        text_lines = []
        for page in result.pages:
            for line in page.lines:
                text_lines.append(line.content)

        extracted_text = "\n".join(text_lines)

        expiration = extract_expiration_date(extracted_text)

        return func.HttpResponse(
            json.dumps({
                "message": "Success",
                "filename": original_name,
                "blob_name": blob_name,
                "result": expiration,
                "extracted_text": extracted_text
            }, indent=2),
            status_code=200,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"}
        )

    except Exception as e:
        logging.error(str(e))
        logging.error(traceback.format_exc())

        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"}
        )