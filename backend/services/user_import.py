"""Strict CSV parsing and case-insensitive account matching for bulk imports."""

import csv
import io
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ValidationError

from backend.database.models import User
from backend.enums import Permission, UserRole
from backend.models.auth import CreateUserRequest

CSV_COLUMNS = ["username", "email", "display_name", "password"]
MAX_CSV_BYTES = 1024 * 1024
MAX_CSV_ROWS = 1000
CSV_TEMPLATE = "username,email,display_name,password\r\n"


class ImportRowResult(BaseModel):
    row: int
    username: str
    email: str | None = None
    status: Literal["ready", "skipped", "error", "created"]
    message: str


class UserImportResponse(BaseModel):
    rows: list[ImportRowResult]
    ready: int = 0
    skipped: int = 0
    errors: int = 0
    created: int = 0


@dataclass
class ParsedUser:
    result: ImportRowResult
    request: CreateUserRequest | None = None
    has_password: bool = False


def parse_user_csv(contents: bytes, users: list[User]) -> list[ParsedUser]:
    if len(contents) > MAX_CSV_BYTES:
        raise ValueError("CSV must be 1 MB or smaller")
    try:
        text = contents.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("CSV must use UTF-8 encoding") from None
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    parsed: list[ParsedUser] = []
    try:
        if next(reader, None) != CSV_COLUMNS:
            raise ValueError("CSV header must be: " + ",".join(CSV_COLUMNS))
        for values in reader:
            row_number = reader.line_num
            if not values or all(not value.strip() for value in values):
                continue
            if len(parsed) >= MAX_CSV_ROWS:
                raise ValueError("CSV may contain at most 1,000 users")
            result = ImportRowResult(
                row=row_number, username=values[0].strip(), status="error", message=""
            )
            entry = ParsedUser(result=result)
            parsed.append(entry)
            if len(values) != len(CSV_COLUMNS):
                result.message = "Expected exactly four columns"
                continue
            data = dict(zip(CSV_COLUMNS, values, strict=True))
            entry.has_password = bool(data["password"].strip())
            try:
                entry.request = CreateUserRequest(
                    username=data["username"],
                    email=data["email"],
                    display_name=data["display_name"],
                    password=data["password"] if entry.has_password else "unused",
                    role=UserRole.USER,
                    permissions=[Permission.REQUEST],
                )
            except ValidationError as exc:
                # Return only validation messages, never password input values.
                result.message = "; ".join(
                    str(error["loc"][0]) + ": " + error["msg"]
                    for error in exc.errors(include_input=False, include_url=False)
                )
                continue
            request = entry.request
            result.username = request.username
            result.email = str(request.email) if request.email else None
            if not entry.has_password and not request.email:
                result.message = "Email is required when password is blank"
                continue
            result.status = "ready"
            result.message = "Ready to create"
    except csv.Error:
        raise ValueError("Malformed CSV: check quotes and column separators") from None
    if not parsed:
        raise ValueError("CSV must contain at least one user")

    usernames = Counter(entry.result.username.lower() for entry in parsed)
    emails = Counter(
        entry.result.email.lower() for entry in parsed if entry.result.email
    )
    for entry in parsed:
        result = entry.result
        if result.status != "ready":
            continue
        username = result.username.lower()
        email = result.email.lower() if result.email else None
        if usernames[username] > 1 or (email and emails[email] > 1):
            result.status = "error"
            result.message = "Duplicate username or email in CSV"
            continue
        email_matches = [
            u for u in users if email and u.email and u.email.lower() == email
        ]
        name_matches = [u for u in users if u.username.lower() == username]
        if len(email_matches) > 1 or len(name_matches) > 1:
            result.status = "error"
            result.message = "Multiple existing accounts match; review manually"
        elif (
            email_matches and name_matches and email_matches[0].id != name_matches[0].id
        ):
            result.status = "error"
            result.message = "Email and username match different accounts"
        elif email_matches:
            result.status = "skipped"
            result.message = "Existing account matched by email; no changes"
        elif name_matches:
            existing_email = (name_matches[0].email or "").lower()
            if email and existing_email != email:
                result.status = "error"
                result.message = (
                    "Username already belongs to an account with a different email"
                )
            else:
                result.status = "skipped"
                result.message = "Existing username; no changes"
    return parsed


def summarize_import(entries: list[ParsedUser]) -> UserImportResponse:
    rows = [entry.result for entry in entries]
    counts = Counter(row.status for row in rows)
    return UserImportResponse(
        rows=rows,
        ready=counts["ready"],
        skipped=counts["skipped"],
        errors=counts["error"],
        created=counts["created"],
    )
