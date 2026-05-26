import os
import sqlite3
from datetime import datetime, timezone

import requests
from fastmcp import FastMCP
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_core.tools import tool
from langgraph.prebuilt import ToolNode

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GOOGLE_CREDENTIALS_FILE = os.path.join(BASE_DIR, "credentials.json")
GOOGLE_TOKEN_FILE = os.path.join(BASE_DIR, "token.json")
GOOGLE_CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
DATA_DIR = "/tmp"

os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "events.db")
CATEGORIES_PATH = os.path.join(os.path.dirname(__file__), "categories.json")

mcp = FastMCP(name="chatbot-server")

search_tool = DuckDuckGoSearchRun()


def get_google_calendar_service():
    """Build an authenticated Google Calendar API service using local OAuth token storage."""
    creds = None

    if os.path.exists(GOOGLE_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, GOOGLE_CALENDAR_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(GOOGLE_CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"Google credentials file not found: {GOOGLE_CREDENTIALS_FILE}"
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                GOOGLE_CREDENTIALS_FILE,
                GOOGLE_CALENDAR_SCOPES,
            )
            creds = flow.run_local_server(port=0)

        with open(GOOGLE_TOKEN_FILE, "w", encoding="utf-8") as token_file:
            token_file.write(creds.to_json())

    return build("calendar", "v3", credentials=creds)


def _normalize_attendees(attendees):
    """Convert attendee email strings into Calendar API attendee objects."""
    if not attendees:
        return []

    normalized = []
    for attendee in attendees:
        if not attendee:
            continue
        if isinstance(attendee, str):
            normalized.append({"email": attendee})
        elif isinstance(attendee, dict) and attendee.get("email"):
            normalized.append({"email": attendee["email"]})
    return normalized

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                amount REAL NOT NULL,
                category TEXT NOT NULL,
                subcategory TEXT DEFAULT '',
                note TEXT DEFAULT ''
            )
            """
        )
        conn.commit()
init_db()

@mcp.tool
def add_expense(date: str, amount: float, category: str, subcategory: str = "", note: str = "") -> dict:
    """Add an expense record to the database."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO events (date, amount, category, subcategory, note)
                VALUES (?, ?, ?, ?, ?)
                """,
                (date, amount, category, subcategory, note),
            )
            conn.commit()
            return {"ok": True, "id": cursor.lastrowid}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@mcp.tool
def list_expenses_by_dates(start_date: str, end_date: str) -> dict:
    """List all expense records from the database."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, date, amount, category, subcategory, note FROM events WHERE date BETWEEN ? AND ?",
                (start_date, end_date)
            )
            rows = cursor.fetchall()
            expenses = [
                {
                    "id": row[0],
                    "date": row[1],
                    "amount": row[2],
                    "category": row[3],
                    "subcategory": row[4],
                    "note": row[5],
                }
                for row in rows
            ]
            return {"ok": True, "expenses": expenses}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    
@mcp.tool
def delete_expense(expense_id: int) -> dict:
    """Delete an expense record from the database by ID."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM events WHERE id = ?", (expense_id,))
            conn.commit()
            if cursor.rowcount == 0:
                return {"ok": False, "error": f"No expense found with id {expense_id}"}
            return {"ok": True, "deleted_id": expense_id}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@mcp.tool
def update_expense(expense_id: int, date: str | None = None, amount: float | None = None, category: str | None = None, subcategory: str | None = None, note: str | None = None) -> dict:
    """Update an existing expense record in the database by ID."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM events WHERE id = ?", (expense_id,))
            if cursor.fetchone() is None:
                return {"ok": False, "error": f"No expense found with id {expense_id}"}

            fields_to_update = []
            values = []
            if date is not None:
                fields_to_update.append("date = ?")
                values.append(date)
            if amount is not None:
                fields_to_update.append("amount = ?")
                values.append(amount)
            if category is not None:
                fields_to_update.append("category = ?")
                values.append(category)
            if subcategory is not None:
                fields_to_update.append("subcategory = ?")
                values.append(subcategory)
            if note is not None:
                fields_to_update.append("note = ?")
                values.append(note)

            if not fields_to_update:
                return {"ok": False, "error": "No fields provided to update."}

            values.append(expense_id)
            sql_query = f"UPDATE events SET {', '.join(fields_to_update)} WHERE id = ?"
            cursor.execute(sql_query, tuple(values))
            conn.commit()
            return {"ok": True, "updated_id": expense_id}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    
@mcp.resource("expense://categories", mime_type="application/json")
def categories():
    """Return the list of expense categories and subcategories."""
    try:
        with open(CATEGORIES_PATH, "r", encoding="utf-8") as f:
            categories_data = f.read()
            return {"ok": True, "categories": categories_data}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    
@mcp.tool
def calculator(first_num: float, second_num: float, operation: str) -> dict:
    """A simple calculator tool that can perform basic arithmetic operations."""
    if operation == "add":
        result = first_num + second_num
    elif operation == "subtract":
        result = first_num - second_num
    elif operation == "multiply":
        result = first_num * second_num
    elif operation == "divide":
        if second_num == 0:
            return {"error": "Error: Division by zero is undefined."}
        result = first_num / second_num
    else:
        return {"error": "Invalid operation. Supported operations are: add, subtract, multiply, divide."}
    return {"first_num": first_num, "second_num": second_num, "operation": operation, "result": result}


@mcp.tool
def get_stock_price(symbol: str) -> dict:
    """
    Fetch latest stock price for a given symbol (e.g. 'AAPL', 'TSLA') using Alpha Vantage with API key in the URL.
    """
    url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey=YIHP08H0B108536X"
    r = requests.get(url)
    return r.json()


@mcp.tool
def schedule_calendar_event(
    summary: str,
    start_time: str,
    end_time: str,
    description: str = "",
    timezone: str = "UTC",
    calendar_id: str = "primary",
    attendees: list[str] | None = None,
) -> dict:
    """Schedule a Google Calendar event."""
    try:
        service = get_google_calendar_service()
        event_body = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start_time, "timeZone": timezone},
            "end": {"dateTime": end_time, "timeZone": timezone},
            "attendees": _normalize_attendees(attendees),
        }

        if not event_body["attendees"]:
            event_body.pop("attendees")

        created_event = (
            service.events()
            .insert(calendarId=calendar_id, body=event_body, sendUpdates="none")
            .execute()
        )

        return {
            "ok": True,
            "event_id": created_event.get("id"),
            "html_link": created_event.get("htmlLink"),
            "calendar_id": calendar_id,
            "summary": summary,
            "start_time": start_time,
            "end_time": end_time,
            "timezone": timezone,
            "attendees": attendees or [],
        }
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "summary": summary,
            "start_time": start_time,
            "end_time": end_time,
            "timezone": timezone,
            "calendar_id": calendar_id,
            "attendees": attendees or [],
        }


@mcp.tool
def list_upcoming_calendar_events(
    max_results: int = 10,
    calendar_id: str = "primary",
) -> dict:
    """List upcoming Google Calendar events from the chosen calendar."""
    try:
        service = get_google_calendar_service()
        now = datetime.now(timezone.utc).isoformat()
        events_result = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=now,
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )

        events = events_result.get("items", [])
        simplified_events = []
        for event in events:
            simplified_events.append(
                {
                    "id": event.get("id"),
                    "summary": event.get("summary", "(No title)"),
                    "start": event.get("start", {}),
                    "end": event.get("end", {}),
                    "html_link": event.get("htmlLink"),
                    "location": event.get("location"),
                    "attendees": event.get("attendees", []),
                }
            )

        return {
            "ok": True,
            "calendar_id": calendar_id,
            "count": len(simplified_events),
            "events": simplified_events,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "calendar_id": calendar_id}


@mcp.tool
def delete_calendar_event(calendar_event_id: str, calendar_id: str = "primary") -> dict:
    """Delete a calendar event by event ID."""
    try:
        service = get_google_calendar_service()
        service.events().delete(calendarId=calendar_id, eventId=calendar_event_id).execute()
        return {"ok": True, "calendar_id": calendar_id, "event_id": calendar_event_id, "deleted": True}
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "calendar_id": calendar_id,
            "event_id": calendar_event_id,
        }


@mcp.tool
def reschedule_calendar_event(
    calendar_event_id: str,
    calendar_id: str = "primary",
    summary: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    description: str | None = None,
    timezone: str | None = None,
    attendees: list[str] | None = None,
) -> dict:
    """Update an existing calendar event's time, title, description, and attendees."""
    try:
        service = get_google_calendar_service()
        existing_event = service.events().get(calendarId=calendar_id, eventId=calendar_event_id).execute()

        if summary is not None:
            existing_event["summary"] = summary
        if description is not None:
            existing_event["description"] = description
        if start_time is not None:
            existing_event.setdefault("start", {})["dateTime"] = start_time
            if timezone is not None:
                existing_event["start"]["timeZone"] = timezone
        if end_time is not None:
            existing_event.setdefault("end", {})["dateTime"] = end_time
            if timezone is not None:
                existing_event["end"]["timeZone"] = timezone
        if timezone is not None:
            existing_event.setdefault("start", {}).setdefault("timeZone", timezone)
            existing_event.setdefault("end", {}).setdefault("timeZone", timezone)
        if attendees is not None:
            normalized_attendees = _normalize_attendees(attendees)
            if normalized_attendees:
                existing_event["attendees"] = normalized_attendees
            elif "attendees" in existing_event:
                existing_event.pop("attendees")

        updated_event = (
            service.events()
            .update(calendarId=calendar_id, eventId=calendar_event_id, body=existing_event, sendUpdates="none")
            .execute()
        )

        return {
            "ok": True,
            "calendar_id": calendar_id,
            "event_id": calendar_event_id,
            "html_link": updated_event.get("htmlLink"),
            "summary": updated_event.get("summary"),
            "start": updated_event.get("start", {}),
            "end": updated_event.get("end", {}),
            "attendees": updated_event.get("attendees", []),
        }
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "calendar_id": calendar_id,
            "event_id": calendar_event_id,
        }


tools = [
    calculator,
    get_stock_price,
    search_tool,
    schedule_calendar_event,
    list_upcoming_calendar_events,
    delete_calendar_event,
    reschedule_calendar_event,
]
tool_node = ToolNode(tools)

if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000)
