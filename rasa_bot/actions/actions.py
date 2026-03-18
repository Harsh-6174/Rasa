import json, os
from dotenv import load_dotenv
from datetime import datetime
from rapidfuzz import process, fuzz
from rasa_sdk import Action, FormValidationAction
from rasa_sdk.events import SlotSet, FollowupAction, ActiveLoop, AllSlotsReset
import re, requests
from requests.auth import HTTPBasicAuth

# Fetch by id or email, ticket creation, user detail fetch, incident-service req, have to do only incident
# after hr/workelevate api answer, ask if it's alright or do we need to raise a ticket
# write agenda, docs, tech used, deliverables
# KEDB - known event db, ROI should be clear
# product - impact - delivery - roi (roadmap for doc)
# We will get - api of known sop, JSON of troubleshooter solutions
# Order to be followed for IT related queries - Check solutions -> known sop -> ticket creation
# For JSON - return ps_command_id, if parent_id = 0 (then it is category)
# give restricted access to user to update status (resolve, cancel)
# try rasa 2.8.13

load_dotenv()

instance = os.getenv("SERVICENOW_INSTANCE")
username = os.getenv("SERVICENOW_USERNAME")
password = os.getenv("SERVICENOW_PASSWORD")
headers = {
    "Accept": "application/json",
    "Content-Type": "application/json"
}

def create_incident_ticket(user_email, short_description, ticket_description, category):
    try:
        user_url = (
            f"https://{instance}.service-now.com/api/now/table/sys_user"
            f"?sysparm_query=email={user_email}"
        )

        response_sys_id = requests.get(
            user_url,
            auth=HTTPBasicAuth(username, password),
            headers=headers,
            timeout=10
        )

        if response_sys_id.status_code != 200:
            return {"error": "Unable to verify user in ServiceNow"}

        data = response_sys_id.json()
        users = data.get("result", [])

        if not users:
            return {"error": f"No user found with Email Id {user_email}"}

        sys_id = users[0].get("sys_id")

        incident_url = f"https://{instance}.service-now.com/api/now/table/incident"

        payload = {
            "caller_id": sys_id,
            "short_description": short_description,
            "description": ticket_description,
            "category": category
        }

        response = requests.post(
            incident_url,
            auth=HTTPBasicAuth(username, password),
            headers=headers,
            json=payload,
            timeout=10
        )

        if response.status_code == 201:
            ticket_data = response.json().get("result", {})
            return {"success": True, "data": ticket_data}

        return {"error": f"Error creating incident ticket: {response.text}"}

    except requests.exceptions.RequestException as e:
        return {"error": f"ServiceNow connection failed: {str(e)}"}

class ValidateCreateTicketForm(FormValidationAction):
    def name(self):
        return "validate_create_ticket_form"

    # def validate_user_email(self, value, dispatcher, tracker, domain):
        pattern = r"^[\w\.-]+@[\w\.-]+\.\w+$"

        if re.match(pattern, value):
            return {"user_email": value}

        dispatcher.utter_message(
            text="That doesn’t look like a valid email. Please enter a correct email ID."
        )
        return {"user_email": None}

    def validate_user_email(self, value, dispatcher, tracker, domain):
        pattern = r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"
        if re.fullmatch(pattern, value.strip()):
            return {
                "user_email": value.strip(),
                "email_retry": False
            }
        # dispatcher.utter_message(text="That doesn’t look like a valid email. Please enter a correct email ID.")
        return {
            "user_email": None,
            "email_retry": True
        }

class ActionCreateTicket(Action):

    def name(self):
        return "action_create_ticket"

    def run(self, dispatcher, tracker, domain):

        user_email = tracker.get_slot("user_email")
        short_description = tracker.get_slot("short_description")
        ticket_description = tracker.get_slot("ticket_description")
        category = tracker.get_slot("category")

        result = create_incident_ticket(
            user_email,
            short_description,
            ticket_description,
            category
        )

        if "error" in result:
            dispatcher.utter_message(
                f"Failed to create the ticket: {result['error']}"
            )

            return [
                SlotSet("user_email", None)
            ]

        ticket_data = result.get("data", {})
        ticket_id = ticket_data.get("number") or ticket_data.get("request_number")

        if not ticket_id:
            dispatcher.utter_message(
                "The ticket was created but the ticket number could not be retrieved."
            )
        else:
            dispatcher.utter_message(
                f"Your ticket has been created with ticket Id - {ticket_id}"
            )

        dispatcher.utter_message(
            "Let me know if you need anything else."
        )

        return [
            SlotSet("short_description", None),
            SlotSet("ticket_description", None),
            SlotSet("category", None),
            SlotSet("email_retry", False)
        ]



def fetch_ticket_by_id(ticket_id):
    incident_state_mapping = {
        "1": "New",
        "2": "In Progress",
        "3": "On Hold",
        "4": "Closed"
    }

    url = f"https://{instance}.service-now.com/api/now/table/incident?sysparm_query=number={ticket_id}"

    try:
        response = requests.get(
            url,
            auth=HTTPBasicAuth(username, password),
            headers=headers,
            timeout=10
        )
    except requests.exceptions.RequestException as e:
        return {"error": f"ServiceNow connection failed: {str(e)}"}

    if response.status_code != 200:
        return {"error": f"Error fetching the ticket with Id {ticket_id}"}

    data = response.json()
    results = data.get("result", [])

    if not results:
        return {"error": f"No ticket found with ID - {ticket_id}"}

    incident = results[0]

    incident_number = incident.get("number")
    description = incident.get("description", "No description available")
    short_description = incident.get("short_description", "No short description available")
    incident_state_number = str(incident.get("incident_state"))

    incident_status = incident_state_mapping.get(incident_state_number, "Unknown")

    return {
        "ticket_id": incident_number,
        "short_description": short_description,
        "description": description,
        "status": incident_status
    }

def fetch_ticket_by_email(user_email):
    incident_state_mapping = {
        "1": "New",
        "2": "In Progress",
        "3": "On Hold",
        "6": "Resolved",
        "7": "Closed"
    }

    url = f"https://{instance}.service-now.com/api/now/table/sys_user?sysparm_query=email={user_email}"

    try:
        response = requests.get(
            url,
            auth=HTTPBasicAuth(username, password),
            headers=headers,
            timeout=10
        )
    except requests.exceptions.RequestException as e:
        return {"error": f"ServiceNow connection failed: {str(e)}"}

    if response.status_code != 200:
        return {"error": "Error fetching user data"}

    data = response.json()
    users = data.get("result", [])

    if not users:
        return {"error": f"No user found with the email {user_email}"}

    sys_id = users[0].get("sys_id")

    incidents_url = (
        f"https://{instance}.service-now.com/api/now/table/incident"
        f"?sysparm_query=caller_id={sys_id}^ORDERBYDESCsys_updated_on"
        f"&sysparm_limit=1"
    )

    try:
        incidents_response = requests.get(
            incidents_url,
            auth=HTTPBasicAuth(username, password),
            headers=headers,
            timeout=10
        )
    except requests.exceptions.RequestException as e:
        return {"error": f"ServiceNow connection failed: {str(e)}"}

    if incidents_response.status_code != 200:
        return {"error": "Error while fetching incidents"}

    incidents_data = incidents_response.json()
    incidents = incidents_data.get("result", [])

    if not incidents:
        return {"error": f"No incidents found for the email {user_email}"}

    latest_incident = incidents[0]

    incident_number = latest_incident.get("number")
    description = latest_incident.get("description", "No description available")
    short_description = latest_incident.get("short_description", "No short description available")
    incident_state_number = str(latest_incident.get("incident_state"))

    incident_status = incident_state_mapping.get(incident_state_number, "Unknown")

    return {
        "ticket_id": incident_number,
        "short_description": short_description,
        "description": description,
        "status": incident_status
    }

class ValidateFetchTicketForm(FormValidationAction):
    def name(self):
        return "validate_fetch_ticket_form"

    def validate_ticket_id_or_email(self, value, dispatcher, tracker, domain):
        incident_id_regex = r"INC\d{6,}"
        email_id_regex = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"

        value = value.strip()
        upper_value = value.upper()

        if re.fullmatch(incident_id_regex, upper_value):
            return {
                "ticket_id_or_email": upper_value,
                "ticket_lookup_retry": False
            }

        if re.fullmatch(email_id_regex, value):
            return {
                "ticket_id_or_email": value,
                "ticket_lookup_retry": False
            }

        return {
            "ticket_id_or_email": None,
            "ticket_lookup_retry": True
        }

class ActionFetchTicket(Action):
    def name(self):
        return "action_fetch_ticket"

    def run(self, dispatcher, tracker, domain):
        ticket_id_or_email = tracker.get_slot("ticket_id_or_email")
        ticket_id = None
        user_email = None

        events = []

        if ticket_id_or_email:
            ticket_id_or_email = ticket_id_or_email.strip()
            incident_id_regex = r"\bINC\d{6,}\b"
            email_id_regex = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"

            incident_id_match = re.search(incident_id_regex, ticket_id_or_email)
            if incident_id_match:
                ticket_id = incident_id_match.group(0).upper()
                events.append(SlotSet("ticket_id", ticket_id))

            email_match = re.search(email_id_regex, ticket_id_or_email)
            if email_match:
                user_email = email_match.group(0)
                events.append(SlotSet("user_email", user_email))

        if ticket_id:
            result = fetch_ticket_by_id(ticket_id)
            if "error" in result:
                dispatcher.utter_message(f"Failed to fetch the ticket: {result['error']}")
                events.append(SlotSet("user_email", None))
            else:
                dispatcher.utter_message(f"Details of ticket {ticket_id}:\n")
                dispatcher.utter_message(
                    f"Ticket Id - {result.get('ticket_id')}\n"
                    f"Short Description - {result.get('short_description')}\n"
                    f"Description - {result.get('description')}\n"
                    f"Status - {result.get('status')}"
                )
                dispatcher.utter_message("Let me know if you need anything else.")

        elif user_email:
            result = fetch_ticket_by_email(user_email)

            if "error" in result:
                dispatcher.utter_message(f"Failed to fetch the ticket: {result['error']}")
                events.append(SlotSet("user_email", None))
            else:
                if result:
                    dispatcher.utter_message(f"Latest ticket associated with email {user_email}:\n")
                    dispatcher.utter_message(
                        f"Ticket Id - {result.get('ticket_id')}\n"
                        f"Short Description - {result.get('short_description')}\n"
                        f"Description - {result.get('description')}\n"
                        f"Status - {result.get('status')}"
                    )
                else:
                    dispatcher.utter_message(f"No tickets found for email {user_email}")
                dispatcher.utter_message("Let me know if you need anything else.")
        else:
            dispatcher.utter_message(
                "Please provide either a ticket ID or an email ID to fetch the tickets."
            )
            events.append(SlotSet("user_email", None))

        events.extend([
            SlotSet("ticket_type", None),
            SlotSet("ticket_id_or_email", None),
            SlotSet("ticket_id", None),
            SlotSet("ticket_lookup_retry", False),
        ])

        return events



def get_tickets_by_email(user_email):
    url = (
        f"https://{instance}.service-now.com/api/now/table/incident"
        f"?sysparm_query=caller_id.email={user_email}^incident_stateNOT IN6,7^ORDERBYDESCsys_created_on"
        f"&sysparm_fields=number,short_description,incident_state"
    )

    try:
        response = requests.get(
            url,
            auth=HTTPBasicAuth(username, password),
            headers=headers,
            timeout=10
        )
    except requests.exceptions.RequestException:
        return []

    if response.status_code != 200:
        return []

    return response.json().get("result", [])

class ActionAskUpdateTicketFormTicketId(Action):
    def name(self):
        return "action_ask_update_ticket_form_ticket_id_update"

    def run(self, dispatcher, tracker, domain):
        user_email = tracker.get_slot("user_email")
        tickets = get_tickets_by_email(user_email) if user_email else []

        if not tickets:
            dispatcher.utter_message(
                "I couldn't find any tickets for your email. Please enter your ticket ID."
            )
            return []

        incident_state_mapping = {
            "1": "New",
            "2": "In Progress",
            "3": "On Hold",
            "6": "Resolved",
            "7": "Closed"
        }

        buttons = []
        for t in tickets:
            state = incident_state_mapping.get(str(t.get("incident_state")), "Unknown")
            short_desc = (t.get("short_description") or "")[:40]
            buttons.append(
                {
                    "title": f"{t.get('number')} | {short_desc} | {state}",
                    "payload": t.get("number")
                }
            )

        dispatcher.utter_message(
            text="Please select the ticket you want to update:",
            buttons=buttons
        )

        return []

class ActionAskUpdateTicketStatusFormTicketId(Action):
    def name(self):
        return "action_ask_update_ticket_status_form_ticket_id_update"

    def run(self, dispatcher, tracker, domain):
        user_email = tracker.get_slot("user_email")
        tickets = get_tickets_by_email(user_email) if user_email else []

        if not tickets:
            dispatcher.utter_message("I couldn't find any tickets for your email. Please enter your ticket ID.")
            return []

        incident_state_mapping = {
            "1": "New",
            "2": "In Progress",
            "3": "On Hold",
            "6": "Resolved",
            "7": "Closed"
        }

        buttons = []
        for t in tickets:
            number = t.get("number")
            short_desc = (t.get("short_description") or "")[:40]
            state = incident_state_mapping.get(
                str(t.get("incident_state")), "Unknown"
            )

            buttons.append(
                {
                    "title": f"{number} | {short_desc} | {state}",
                    "payload": number
                }
            )

        dispatcher.utter_message(
            text="Please select the ticket you want to update:",
            buttons=buttons
        )

        return []

def update_ticket_description(ticket_id, new_description):
    url_sys_id = (
        f"https://{instance}.service-now.com/api/now/table/incident"
        f"?sysparm_query=number={ticket_id}"
    )

    try:
        response_sys_id = requests.get(
            url_sys_id,
            auth=HTTPBasicAuth(username, password),
            headers=headers,
            timeout=10
        )
    except requests.exceptions.RequestException as e:
        return {"error": f"ServiceNow connection failed: {str(e)}"}

    if response_sys_id.status_code != 200:
        return {"error": f"Unable to fetch incident {ticket_id}"}

    results = response_sys_id.json().get("result", [])
    if not results:
        return {"error": f"No incident found with ID {ticket_id}"}

    sys_id = results[0].get("sys_id")
    url = f"https://{instance}.service-now.com/api/now/table/incident/{sys_id}"
    data = {"description": new_description}

    try:
        response = requests.put(
            url,
            auth=HTTPBasicAuth(username, password),
            headers=headers,
            json=data,
            timeout=10
        )
    except requests.exceptions.RequestException as e:
        return {"error": f"ServiceNow connection failed: {str(e)}"}

    if response.status_code != 200:
        return {"error": f"Error updating ticket with ID {ticket_id}"}

    updated_incident = response.json().get("result")

    if not updated_incident:
        return {"error": "No result returned from the API"}

    incident_number = updated_incident.get("number", ticket_id)

    return {
        "ticket_id": incident_number,
        "description": new_description,
        "status": "Updated Successfully"
    }

class ValidateUpdateTicketForm(FormValidationAction):
    def name(self):
        return "validate_update_ticket_form"

    def validate_user_email(self, value, dispatcher, tracker, domain):
        pattern = r"^[\w\.-]+@[\w\.-]+\.\w+$"

        if re.fullmatch(pattern, value.strip()):
            return {
                "user_email": value.strip(),
                "email_retry": False
            }

        return {
            "user_email": None,
            "email_retry": True
        }

    def validate_ticket_id_update(self, value, dispatcher, tracker, domain):
        value = value.strip().upper()

        if re.fullmatch(r"INC\d{6,}", value):
            return {
                "ticket_id_update": value,
                "ticket_update_retry": False
            }

        return {
            "ticket_id_update": None,
            "ticket_update_retry": True
        }

class ActionUpdateTicketDescription(Action):
    def name(self):
        return "action_update_ticket_description"

    def run(self, dispatcher, tracker, domain):
        ticket_id = tracker.get_slot("ticket_id_update")
        if ticket_id:
            ticket_id = ticket_id.upper()
        
        new_description = tracker.get_slot("new_description")

        result = update_ticket_description(ticket_id, new_description)

        if "error" in result:
            dispatcher.utter_message(
                f"Failed to update the ticket : {result['error']}"
            )
        else:
            dispatcher.utter_message(
                f"Ticket ID {ticket_id} has been updated with the new description"
            )

        dispatcher.utter_message("Let me know if you need anything else.")

        return [
            SlotSet("ticket_id_update", None),
            SlotSet("new_description", None),
            SlotSet("ticket_update_retry", False),
            SlotSet("email_retry", False),
        ]

def update_ticket_status(ticket_id, new_status):
    if not new_status:
        return {"error": "Missing status."}

    ticket_id = ticket_id.strip().upper()
    new_status = new_status.lower().strip()

    if new_status in ["resolve", "resolved"]:
        new_status = "resolved"
    elif new_status in ["close", "closed"]:
        new_status = "closed"

    status_map = {
        "resolved": "6",
        "closed": "7"
    }

    if new_status not in status_map:
        return {"error": "Only resolve or close is allowed."}

    url_sys_id = (
        f"https://{instance}.service-now.com/api/now/table/incident"
        f"?sysparm_query=number={ticket_id}"
    )

    try:
        response_sys_id = requests.get(
            url_sys_id,
            auth=HTTPBasicAuth(username, password),
            headers=headers,
            timeout=10
        )
    except requests.exceptions.RequestException as e:
        return {"error": f"ServiceNow connection failed: {str(e)}"}

    if response_sys_id.status_code != 200:
        return {"error": f"Unable to fetch incident {ticket_id}"}

    incidents = response_sys_id.json().get("result", [])

    if not incidents:
        return {"error": f"No incident found with ID {ticket_id}"}

    sys_id = incidents[0].get("sys_id")
    url = f"https://{instance}.service-now.com/api/now/table/incident/{sys_id}"

    data = {
        "state": status_map[new_status]
    }

    if new_status == "resolved":
        data.update({
            "close_code": "Resolved by caller",
            "close_notes": "Resolved via chatbot after user confirmation.",
            "resolved_by": username,
            "resolved_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        })

    if new_status == "closed":
        data.update({
            "close_code": "Resolved by caller",
            "close_notes": "Closed via chatbot after user confirmation."
        })

    try:
        response = requests.patch(
            url,
            auth=HTTPBasicAuth(username, password),
            headers=headers,
            json=data,
            timeout=10
        )
    except requests.exceptions.RequestException as e:
        return {"error": f"ServiceNow connection failed: {str(e)}"}

    if response.status_code != 200:
        return {
            "error": f"ServiceNow error ({response.status_code}): {response.text}"
        }

    result = response.json().get("result", {})

    return {
        "ticket_id": result.get("number", ticket_id),
        "status": new_status,
        "status_update": "Successfully updated"
    }

class ValidateUpdateTicketStatusForm(FormValidationAction):
    def name(self):
        return "validate_update_ticket_status_form"

    def validate_user_email(self, value, dispatcher, tracker, domain):
        pattern = r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"

        if re.fullmatch(pattern, value.strip()):
            return {
                "user_email": value.strip(),
                "email_retry": False
            }

        return {
            "user_email": None,
            "email_retry": True
        }

    def validate_ticket_id_update(self, value, dispatcher, tracker, domain):
        value = value.strip().upper()

        if re.fullmatch(r"INC\d{6,}", value):
            return {
                "ticket_id_update": value,
                "ticket_retry": False
            }

        return {
            "ticket_id_update": None,
            "ticket_retry": True
        }

    def validate_new_status(self, value, dispatcher, tracker, domain):
        value = value.strip().lower()

        status_map = {
            "resolve": "Resolved",
            "resolved": "Resolved",
            "close": "Closed",
            "closed": "Closed"
        }

        if value in status_map:
            return {
                "new_status": status_map[value],
                "status_retry": False
            }

        return {
            "new_status": None,
            "status_retry": True
        }

class ActionUpdateTicketStatus(Action):
    def name(self):
        return "action_update_ticket_status"

    def run(self, dispatcher, tracker, domain):
        ticket_id = tracker.get_slot("ticket_id_update")
        new_status = tracker.get_slot("new_status")

        result = update_ticket_status(ticket_id, new_status)

        if "error" in result:
            dispatcher.utter_message(
                f"Failed to update the ticket status: {result['error']}"
            )
        else:
            dispatcher.utter_message(
                f"Ticket ID {result['ticket_id']} has been updated to {new_status}."
            )

        dispatcher.utter_message("Let me know if you need anything else.")

        return [
            SlotSet("ticket_id_update", None),
            SlotSet("new_status", None),
            SlotSet("email_retry", False),
            SlotSet("ticket_retry", False),
            SlotSet("status_retry", False),
        ]



def fetch_user_tickets(user_email, num_tickets=5):
    url_sys_id = (
        f"https://{instance}.service-now.com/api/now/table/sys_user"
        f"?sysparm_query=email={user_email}"
    )

    try:
        response_sys_id = requests.get(
            url_sys_id,
            auth=HTTPBasicAuth(username, password),
            headers=headers,
            timeout=10
        )
    except requests.exceptions.RequestException as e:
        return {"error": f"ServiceNow connection failed: {str(e)}"}

    if response_sys_id.status_code != 200:
        return {"error": "Error fetching user data"}

    data = response_sys_id.json()
    users = data.get("result", [])

    if not users:
        return {"error": f"No user found with Email Id {user_email}"}

    sys_id = users[0].get("sys_id")
    incident_state_mapping = {
        "1": "New",
        "2": "In Progress",
        "3": "On Hold",
        "6": "Resolved",
        "7": "Closed"
    }

    incidents_url = (
        f"https://{instance}.service-now.com/api/now/table/incident"
        f"?sysparm_query=caller_id={sys_id}^ORDERBYDESCsys_created_on"
        f"&sysparm_limit={num_tickets}"
    )

    try:
        incidents_response = requests.get(
            incidents_url,
            auth=HTTPBasicAuth(username, password),
            headers=headers,
            timeout=10
        )
    except requests.exceptions.RequestException as e:
        return {"error": f"ServiceNow connection failed: {str(e)}"}

    if incidents_response.status_code != 200:
        return {"error": "Error fetching incidents"}

    incidents_data = incidents_response.json()
    incidents = incidents_data.get("result", [])

    if not incidents:
        return []

    tickets = []
    for incident in incidents:
        incident_number = incident.get("number")
        description = incident.get("description", "No description available")

        state_number = str(incident.get("incident_state", ""))
        status = incident_state_mapping.get(state_number, "Unknown")

        tickets.append({
            "ticket_id": incident_number,
            "description": description,
            "status": status
        })

    return tickets

class ValidateFetchLastTicketsForm(FormValidationAction):
    def name(self):
        return "validate_fetch_last_tickets_form"

    def validate_user_email(self, value, dispatcher, tracker, domain):
        pattern = r"^[\w\.-]+@[\w\.-]+\.\w+$"

        if re.match(pattern, value):
            return {"user_email": value.strip().lower()}

        dispatcher.utter_message(
            text="That doesn’t look like a valid email. Please enter a correct one."
        )
        return {"user_email": None}

    def validate_num_tickets(self, value, dispatcher, tracker, domain):
        try:
            n = int(value)
            if n <= 0:
                raise ValueError

            if n > 20:
                return {
                    "num_tickets": None,
                    "num_tickets_retry": "limit"
                }
            return {
                "num_tickets": n,
                "num_tickets_retry": None
            }
        except Exception:
            return {
                "num_tickets": None,
                "num_tickets_retry": "invalid"
            }

class ActionFetchLastTickets(Action):
    def name(self):
        return "action_fetch_last_tickets"

    def run(self, dispatcher, tracker, domain):
        user_email = tracker.get_slot("user_email")
        num_tickets = tracker.get_slot("num_tickets") or 5

        result = fetch_user_tickets(user_email, int(num_tickets))

        if isinstance(result, dict) and "error" in result:
            dispatcher.utter_message(f"Failed to fetch tickets: {result['error']}")
        elif not result:
            dispatcher.utter_message(f"No tickets found for the user with Email ID {user_email}")
        else:
            dispatcher.utter_message(f"Here are your last {len(result)} tickets:\n")
            for ticket in result:
                dispatcher.utter_message(
                    f"Ticket ID: {ticket.get('ticket_id')}\n"
                    f"Description: {ticket.get('description')}\n"
                    f"Status: {ticket.get('status')}\n"
                )

        dispatcher.utter_message("Is there anything else I can help you with?")
        return [
            SlotSet("num_tickets", None),
            SlotSet("num_tickets_retry", False),
        ]



class ActionGetHRResponse(Action):
    def name(self):
        return "action_get_hr_response"

    def run(self, dispatcher, tracker, domain):
        user_query = tracker.latest_message.get("text")

        domain_name = os.getenv("PROGRESSIVE_DOMAIN")
        bearer_token = os.getenv("BEARER_TOKEN")
        url = os.getenv("API_URL")

        payload = {
            "inputs": {"domain_name": domain_name},
            "query": user_query,
            "response_mode": "blocking",
            "conversation_id": "",
            "user": "abc-123",
            "files": [
                {
                    "type": "image",
                    "transfer_method": "remote_url",
                    "url": "https://www.workelevate.com/images/fav-icon.png"
                }
            ]
        }

        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json"
        }

        try:
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=15
            )

            if response.status_code != 200:
                dispatcher.utter_message(
                    "I couldn't retrieve the HR information right now."
                )
                return []

            response_data = response.json()
            raw_answer = response_data.get("answer", "")

            match = re.search(r'</think>(.*)', raw_answer, re.DOTALL)
            clean_answer = match.group(1).strip() if match else raw_answer

            dispatcher.utter_message(clean_answer)
            dispatcher.utter_message("Are you happy with the solution?")
        except requests.exceptions.RequestException as e:
            dispatcher.utter_message("There was an error with the HR API request.")
            print(f"HR API error: {e}")

        return [
            SlotSet("hr_query_completed", True),
            SlotSet("awaiting_satisfaction_feedback", "hr"),
            SlotSet("user_query", user_query)
        ]

class ActionHandleUserSatisfaction(Action):
    def name(self):
        return "action_handle_user_satisfaction"

    def run(self, dispatcher, tracker, domain):
        intent = tracker.latest_message.get("intent", {}).get("name")
        user_query = tracker.get_slot("user_query") or "HR / WorkElevate query"

        if intent == "user_satisfaction_positive":
            dispatcher.utter_message("Great! I'm glad I could help. Let me know if you need anything else.")
            return [
                SlotSet("hr_query_completed", None),
                SlotSet("we_query_completed", None),
                SlotSet("awaiting_satisfaction_feedback", None),
                ActiveLoop(None),
                FollowupAction("action_listen")
            ]

        if intent == "user_satisfaction_negative":
            dispatcher.utter_message(
                "Sorry to hear that! I'll raise a ticket for you right away."
            )

            return [
                SlotSet("short_description", "Unresolved HR / WorkElevate query"),
                SlotSet(
                    "ticket_description",
                    f"User query:\n{user_query}\n\nUser was not satisfied with the response."
                ),
                SlotSet("category", "Inquiry / Help"),
                SlotSet("hr_query_completed", None),
                SlotSet("we_query_completed", None),
                SlotSet("awaiting_satisfaction_feedback", None),
                ActiveLoop(None),
                FollowupAction("create_ticket_form")
            ]

        dispatcher.utter_message("Please reply with yes or no.")
        return [FollowupAction("action_listen")]

class ActionGetWorkElevateResponse(Action):
    def name(self):
        return "action_get_workelevate_response"

    def run(self, dispatcher, tracker, domain):
        user_query = tracker.latest_message.get("text")
        domain_name = os.getenv("WORKELEVATE_DOMAIN")
        bearer_token = os.getenv("BEARER_TOKEN")
        url = os.getenv("API_URL")

        payload = {
            "inputs": {"domain_name": domain_name},
            "query": user_query,
            "response_mode": "blocking",
            "conversation_id": "",
            "user": "abc-123",
            "files": [
                {
                    "type": "image",
                    "transfer_method": "remote_url",
                    "url": "https://www.workelevate.com/images/fav-icon.png"
                }
            ]
        }

        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json"
        }

        try:
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=15
            )

            if response.status_code != 200:
                dispatcher.utter_message(
                    "I couldn't retrieve the WorkElevate information right now."
                )
                return []

            response_data = response.json()
            raw_answer = response_data.get("answer", "")

            match = re.search(r'</think>(.*)', raw_answer, re.DOTALL)
            clean_answer = match.group(1).strip() if match else raw_answer

            dispatcher.utter_message(clean_answer)
            dispatcher.utter_message("Are you happy with the solution?")
        except requests.exceptions.RequestException as e:
            dispatcher.utter_message("There was an error with the WorkElevate API request.")
            print(f"WorkElevate API error: {e}")

        return [
            SlotSet("we_query_completed", True),
            SlotSet("awaiting_satisfaction_feedback", "we"),
            SlotSet("user_query", user_query)
        ]

class ActionFallback(Action):
    def name(self):
        return "action_default_fallback"

    def run(self, dispatcher, tracker, domain):
        dispatcher.utter_message(
            text="Sorry, I didn't understand that. Please rephrase or ask something related to HR policies, WorkElevate, or any issue you are facing."
        )
        return [FollowupAction("action_listen")]



def schedule_agent_job(user_identity, item_id, action_code, custom_job_name=None):
    url = "https://dev.workelevate.com/api/Chatbot/JobScheduler"

    payload = {
        "user_identity": user_identity,
        "item_id": str(item_id),
        "action_code": action_code,
        "retry_count": 0,
        "custom_job_name": custom_job_name or ""
    }

    token = os.getenv("JOB_SCHEDULER_SYNC_DATA_BEARER_TOKEN")
    headers = {
        "accept": "text/plain",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json-patch+json"
    }

    response = requests.post(
        url,
        json=payload,
        headers=headers,
        timeout=10
    )

    response.raise_for_status()

    try:
        return response.json()
    except ValueError:
        return response.text

class ValidateEmailForm(FormValidationAction):
    def name(self):
        return "validate_email_form"

    def validate_user_email(self, value, dispatcher, tracker, domain):
        value = value.strip()
        pattern = r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"

        if re.fullmatch(pattern, value):
            return {"user_email": value}

        dispatcher.utter_message(
            text="That doesn’t look like a valid email. Please enter a correct one."
        )

        return {"user_email": None}

class ActionFindTroubleshooter(Action):
    def name(self):
        return "action_find_troubleshooter"

    def run(self, dispatcher, tracker, domain):
        user_query = tracker.latest_message.get("text", "").strip()

        if not user_query:
            dispatcher.utter_message("Please describe your issue.")
            return [FollowupAction("action_listen")]

        try:
            response = requests.post(
                "http://localhost:8000/match",
                json={"query": user_query},
                timeout=15
            )
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            dispatcher.utter_message(
                f"I'm having trouble analyzing your issue right now. ---- {e}"
            )
            return [FollowupAction("action_listen")]

        matches = data.get("matches", [])
        if not matches:
            dispatcher.utter_message("I couldn't find a matching troubleshooter. Let me try a detailed solution.")

            return [
                SlotSet("user_query", user_query),
                SlotSet("troubleshooter_query_completed", True),
                SlotSet("awaiting_satisfaction_feedback", None),
                FollowupAction("action_get_troubleshooter_sop")
            ]

        buttons = []
        for match in matches:
            name = match.get("name")
            ps_id = match.get("ps_command_id")

            payload_dict = {
                "selected_troubleshooter": name,
                "selected_troubleshooter_ps_id": match.get("ps_command_id"),
                "selected_troubleshooter_id": match.get("troubleshooter_id")
            }
            buttons.append({
                "title": name,
                "payload": "/select_troubleshooter" + json.dumps(payload_dict)
            })

        buttons.append({
            "title": "My issue isn’t listed here",
            "payload": f'/select_troubleshooter{{"selected_troubleshooter":"__NOT_LISTED__"}}'
        })
        dispatcher.utter_message(
            text="I found these relevant troubleshooters. Please select one to run:",
            buttons=buttons
        )

        return [
            SlotSet("troubleshooter_query_completed", True),
            SlotSet("user_query", user_query),
            SlotSet("awaiting_satisfaction_feedback", "ts_select"),
            FollowupAction("action_listen")
        ]

class ActionRunSelectedTroubleshooter(Action):
    def name(self):
        return "action_run_selected_troubleshooter"

    def run(self, dispatcher, tracker, domain):
        t = tracker.get_slot("selected_troubleshooter")
        if not t:
            dispatcher.utter_message("No troubleshooter was selected.")
            return [FollowupAction("action_listen")]

        if t == "__NOT_LISTED__":
            return [
                SlotSet("awaiting_satisfaction_feedback", None),
                FollowupAction("action_get_troubleshooter_sop")
            ]

        email = tracker.get_slot("user_email")
        if not email:
            return [FollowupAction("email_form")]
        
        email = email.split("@")[0]

        ps_id = tracker.get_slot("selected_troubleshooter_ps_id")
        ts_id = tracker.get_slot("selected_troubleshooter_id")

        if ps_id and str(ps_id) != "0":
            item_id = ps_id
        else:
            item_id = ts_id

        if not item_id:
            dispatcher.utter_message("Unable to run this troubleshooter right now.")
            return [FollowupAction("action_listen")]

        try:
            schedule_agent_job(
                user_identity=email,
                item_id=str(item_id),
                action_code="TRBL",
                custom_job_name=f"Troubleshooter - {t}"
            )
        except Exception:
            dispatcher.utter_message("Failed to Schedule troubleshooter job.")
            return [FollowupAction("action_listen")]

        dispatcher.utter_message(
            f"The troubleshooter '{t}' has been scheduled successfully."
        )
        dispatcher.utter_message(
            "I’ll notify you once it completes. Did this resolve your issue?"
        )

        return [
            SlotSet("selected_troubleshooter", None),
            SlotSet("selected_troubleshooter_ps_id", None),
            SlotSet("selected_troubleshooter_id", None),
            SlotSet("awaiting_satisfaction_feedback", "ts_list"),
            FollowupAction("action_listen")
        ]

class ActionGetTroubleshooterSOP(Action):
    def name(self):
        return "action_get_troubleshooter_sop"

    def run(self, dispatcher, tracker, domain):
        user_query = tracker.get_slot("user_query")
        domain_name = os.getenv("PROGRESSIVE_DOMAIN")
        BEARER_TOKEN = os.getenv("BEARER_TOKEN")

        url = os.getenv("API_URL")

        payload = {
            "inputs": {"domain_name": domain_name},
            "query": user_query,
            "response_mode": "blocking", #streaming
            "conversation_id": "",
            "user": "abc-123",
            "files": [
                {
                    "type": "image",
                    "transfer_method": "remote_url",
                    "url": "https://www.workelevate.com/images/fav-icon.png"
                }
            ]
        }
        
        headers = {
            "Authorization": f"Bearer {BEARER_TOKEN}",
            "Content-Type": "application/json"
        }

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=15)
            response_data = response.json()
            raw_answer = response_data.get("answer", "")
            clean_answer = re.search(r'</think>(.*)', raw_answer, re.DOTALL)
            dispatcher.utter_message(clean_answer.group(1).strip())

            dispatcher.utter_message("Did this solution work for you?")
        except Exception as e:
            dispatcher.utter_message(
                "There was an error while fetching the SOP. Please try again."
            )
            print(f"SOP API error: {e}")
        return [
            SlotSet("awaiting_satisfaction_feedback", "ts_sop"),
            FollowupAction("action_listen")
        ]

class ActionHandleUserSatisfactionTroubleShooter(Action):
    def name(self):
        return "action_handle_user_satisfaction_troubleshooter"

    def run(self, dispatcher, tracker, domain):
        intent = tracker.latest_message.get("intent", {}).get("name")
        stage = tracker.get_slot("awaiting_satisfaction_feedback")
        user_query = tracker.get_slot("user_query") or "Technical issue"

        is_positive = intent == "user_satisfaction_positive"
        is_negative = intent == "user_satisfaction_negative"

        if not is_positive and not is_negative:
            dispatcher.utter_message("Please reply with yes or no.")
            return [FollowupAction("action_listen")]

        if stage == "ts_select":
            return [FollowupAction("action_run_selected_troubleshooter")]

        if stage == "ts_not_found":
            if is_positive and not is_negative:
                dispatcher.utter_message("I'll raise a ticket for you.")

                return [
                    SlotSet("short_description", "No troubleshooter available"),
                    SlotSet(
                        "ticket_description",
                        f"User issue:\n{user_query}\n\n"
                        "No relevant troubleshooter was found.\n"
                        "User requested ticket creation."
                    ),
                    SlotSet("category", "Technical"),

                    SlotSet("awaiting_satisfaction_feedback", None),
                    SlotSet("troubleshooter_query_completed", None),

                    ActiveLoop(None),
                    FollowupAction("create_ticket_form")
                ]

            if is_negative:
                dispatcher.utter_message("Alright. Let me know if you need anything else.")
                return [
                    SlotSet("awaiting_satisfaction_feedback", None),
                    SlotSet("troubleshooter_query_completed", None),
                    ActiveLoop(None),
                    FollowupAction("action_listen")
                ]

        if stage == "ts_list":
            if is_positive and not is_negative:
                dispatcher.utter_message("Great! Let me know if you need anything else.")
                return [
                    SlotSet("awaiting_satisfaction_feedback", None),
                    SlotSet("troubleshooter_query_completed", None),
                    ActiveLoop(None),
                    FollowupAction("action_listen")
                ]

            if is_negative:
                return [
                    SlotSet("awaiting_satisfaction_feedback", None),
                    FollowupAction("action_get_troubleshooter_sop")
                ]

        if stage == "ts_sop":
            if is_positive and not is_negative:
                dispatcher.utter_message("Glad that helped! Let me know if you need anything else.")
                return [
                    SlotSet("awaiting_satisfaction_feedback", None),
                    SlotSet("troubleshooter_query_completed", None),
                    ActiveLoop(None),
                    FollowupAction("action_listen")
                ]

            if is_negative:
                dispatcher.utter_message("I’ll raise a ticket for you.")

                return [
                    SlotSet("short_description", f"{user_query} - Troubleshooter and SOP did not resolve issue"),
                    SlotSet(
                        "ticket_description",
                        f"User issue:\n{user_query}\n"
                        "Troubleshooter and SOP were provided.\n"
                        "User is still facing the issue and requested ticket creation."
                    ),
                    SlotSet("category", "Technical"),

                    SlotSet("awaiting_satisfaction_feedback", None),
                    SlotSet("troubleshooter_query_completed", None),

                    ActiveLoop(None),
                    FollowupAction("create_ticket_form")
                ]

        return [
            SlotSet("awaiting_satisfaction_feedback", None),
            ActiveLoop(None),
            FollowupAction("action_listen")
        ]



class ValidateSoftwareRequestForm(FormValidationAction):
    def name(self):
        return "validate_software_request_form"

    def validate_user_email(self, value, dispatcher, tracker, domain):
        value = (value or "").strip()
        pattern = r"^[\w\.-]+@[\w\.-]+\.\w+$"

        if re.fullmatch(pattern, value):
            return {"user_email": value}

        dispatcher.utter_message(
            text="That doesn’t look like a valid email. Please enter a correct one."
        )

        return {"user_email": None}

    def validate_software_name(self, value, dispatcher, tracker, domain):
        software = (value or "").strip()
        if software:
            return {"software_name": software}

        dispatcher.utter_message(text="Please enter the software name.")
        return {"software_name": None}

class ActionHandleSoftwareRequest(Action):
    def name(self):
        return "action_handle_software_request"

    def run(self, dispatcher, tracker, domain):
        software_query = tracker.get_slot("software_name")
        confirmed_software = tracker.get_slot("confirmed_software_name")
        email = tracker.get_slot("user_email")

        if confirmed_software:
            software_name = confirmed_software

            matches = resolve_software_matches(software_name)
            if not matches:
                dispatcher.utter_message("1. I couldn’t find 1 that software in our approved catalog. I’ll raise a ticket for you.")
                return [
                    SlotSet("short_description", f"Software installation request - {software_name} not found"),
                    SlotSet(
                        "ticket_description",
                        f"User requested installation of '{software_name}'.\n"
                        "The software was not found in the approved catalog."
                    ),
                    SlotSet("category", "Software"),
                    SlotSet("software_name", None),
                    SlotSet("confirmed_software_name", None),
                    ActiveLoop(None),
                    FollowupAction("create_ticket_form")
                ]

            software_name, software_info = matches[0]

            events = [
                SlotSet("software_name", None)
            ]

            if (not software_info.get("is_active")) or (not software_info.get("allow_to_user")):
                dispatcher.utter_message(
                    f"{software_name.title()} is not allowed on company devices."
                )
                dispatcher.utter_message(
                    "Is there anything else I can help you with?"
                )
                return events + [
                    SlotSet("software_name", None),
                    SlotSet("confirmed_software_name", None),
                    ActiveLoop(None),
                    FollowupAction("action_listen")
                ]

            if (not software_info.get("allow_to_automation")) or software_info.get("is_consent"):
                dispatcher.utter_message(
                    f"{software_name.title()} requires approval before installation.\n"
                    "I’ll raise a request for approval."
                )

                return events + [
                    SlotSet("short_description", f"Software request: {software_name.title()}"),
                    SlotSet(
                        "ticket_description",
                        f"User requested installation of {software_name.title()}.\n"
                        f"Vendor: {software_info.get('vendor')}\n"
                        f"Version: {software_info.get('version')}\n"
                        f"Approval required."
                    ),
                    SlotSet("category", "Software"),
                    SlotSet("software_name", None),
                    SlotSet("confirmed_software_name", None),
                    ActiveLoop(None),
                    FollowupAction("create_ticket_form")
                ]
            
            email = email.split("@")[0]

            try:
                schedule_agent_job(
                    user_identity=email,
                    item_id=software_info.get("software_id"),
                    action_code="SFT",
                    custom_job_name=f"{software_name.capitalize()} Installation"
                )
            except Exception:
                dispatcher.utter_message("Failed to schedule software installation.")
                return [FollowupAction("action_listen")]

            dispatcher.utter_message(f"The software '{software_name}' has been scheduled successfully for installation.")
            dispatcher.utter_message("I’ll notify you once it completes. Did this resolve your issue?")

            return events + [
                SlotSet("awaiting_satisfaction_feedback", "software_install"),
                SlotSet("user_query", f"Software installation: {software_name}"),
                SlotSet("software_name", None),
                SlotSet("confirmed_software_name", None),
                ActiveLoop(None),
                FollowupAction("action_listen")
            ]

        if not software_query:
            dispatcher.utter_message("I couldn’t identify the software. I’ll raise a ticket for you.")

            return [
                SlotSet("short_description", "Software installation request - software not specified"),
                SlotSet(
                    "ticket_description",
                    "User requested software installation but did not specify the software name.\n"
                    "Please contact the user to confirm the required software."
                ),
                SlotSet("category", "Software"),
                SlotSet("software_name", None),
                ActiveLoop(None),
                FollowupAction("create_ticket_form")
            ]

        matches = resolve_software_matches(software_query)

        if not matches:
            dispatcher.utter_message("I couldn’t find that 2 software in our approved catalog. I’ll raise a ticket for you.")

            return [
                SlotSet("short_description", f"Software installation request - {software_query.title()} not found"),
                SlotSet(
                    "ticket_description",
                    f"User requested installation of '{software_query.title()}'.\n"
                    "The software was not found in the approved catalog."
                ),
                SlotSet("category", "Software"),
                SlotSet("software_name", None),
                ActiveLoop(None),
                FollowupAction("create_ticket_form")
            ]

        if len(matches) > 1:
            buttons = [
                {
                    "title": name.title(),
                    "payload": f'/inform{{"confirmed_software_name":"{name}"}}'
                }
                for name, _ in matches
            ]

            dispatcher.utter_message(
                text="I found multiple matching softwares. Please choose one:",
                buttons=buttons
            )

            return [
                ActiveLoop(None),
                FollowupAction("action_listen")
            ]

        software_name, software_info = matches[0]

        events = [SlotSet("software_name", None)]

        if (not software_info.get("is_active")) or (not software_info.get("allow_to_user")):
            dispatcher.utter_message(
                f"{software_name.title()} is not allowed on company devices."
            )
            dispatcher.utter_message(
                "Is there anything else I can help you with?"
            )
            return events + [
                SlotSet("confirmed_software_name", None),
                ActiveLoop(None),
                FollowupAction("action_listen")
            ]

        if (not software_info.get("allow_to_automation")) or software_info.get("is_consent"):
            dispatcher.utter_message(
                f"{software_name.title()} requires approval before installation.\n"
                "I’ll raise a request for approval."
            )

            return events + [
                SlotSet("short_description", f"Software request: {software_name.title()}"),
                SlotSet(
                    "ticket_description",
                    f"User requested installation of {software_name.title()}.\n"
                    f"Vendor: {software_info.get('vendor')}\n"
                    f"Version: {software_info.get('version')}\n"
                    f"Approval required."
                ),
                SlotSet("category", "Software"),
                SlotSet("software_name", None),
                SlotSet("confirmed_software_name", None),
                ActiveLoop(None),
                FollowupAction("create_ticket_form")
            ]

        email = email.split("@")[0]

        try:
            schedule_agent_job(
                user_identity=email,
                item_id=software_info.get("software_id"),
                action_code="SFT",
                custom_job_name=f"{software_name.capitalize()} Installation"
            )
        except Exception:
            dispatcher.utter_message("Failed to schedule software installation.")
            return [FollowupAction("action_listen")]

        dispatcher.utter_message(f"The software '{software_name}' has been scheduled successfully for installation.")
        dispatcher.utter_message("I’ll notify you once it completes. Did this resolve your issue?")

        return events + [
            SlotSet("awaiting_satisfaction_feedback", "software_install"),
            SlotSet("user_query", f"Software installation: {software_name}"),
            SlotSet("software_name", None),
            SlotSet("confirmed_software_name", None),
            ActiveLoop(None),
            FollowupAction("action_listen")
        ]

def get_action_list(sync_type):
    url = "https://dev.workelevate.com/api/Chatbot/SyncActionData"

    payload = {
        'machine_name': '',
        'domain_name': 'progressive.in',
        'user_name': 'harsh.vardhan',
        'sync_type': f'{sync_type}',
        'domain_id': 2,
        'platform_id': 1
    }

    headers = {
        "accept": "*/*",
        "Authorization": f"Bearer {os.getenv('JOB_SCHEDULER_SYNC_DATA_BEARER_TOKEN')}",
        "Content-Type": "application/json-patch+json"
    }

    try:
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=10
        )

        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"[SYNC ACTION ERROR] {e}")
        return []

def get_software_catalog_map():
    data = get_action_list(sync_type=2)
    catalog = {}

    for software in data:
        name = (
            software.get("software_display_name")
            or software.get("software_name")
            or ""
        ).strip()

        if not name:
            continue
        catalog[name] = software

    return catalog

def resolve_software_matches(software_name: str, limit: int = 5, threshold: int = 70):
    catalog = get_software_catalog_map()
    matches = process.extract(
        software_name,
        catalog.keys(),
        limit=limit,
        scorer=fuzz.partial_ratio,
        processor=lambda s: s.lower()
    )

    results = []
    for best_match, score, _ in matches:
        if score < threshold:
            continue

        results.append((best_match, catalog[best_match]))

    return results

class ActionHandleUserSatisfactionProvisioning(Action):
    def name(self):
        return "action_handle_user_satisfaction_provisioning"

    def run(self, dispatcher, tracker, domain):
        intent = tracker.latest_message.get("intent", {}).get("name")
        stage = tracker.get_slot("awaiting_satisfaction_feedback")
        user_query = tracker.get_slot("user_query") or "Provisioning request"

        is_positive = intent == "user_satisfaction_positive"
        is_negative = intent == "user_satisfaction_negative"

        if not is_positive and not is_negative:
            dispatcher.utter_message("Please reply with yes or no.")
            return [FollowupAction("action_listen")]

        if is_positive and not is_negative:
            dispatcher.utter_message("Great. Let me know if you need anything else.")

            return [
                SlotSet("awaiting_satisfaction_feedback", None),
                ActiveLoop(None),
                FollowupAction("action_listen")
            ]

        if is_negative:
            dispatcher.utter_message("I’ll raise a ticket for you.")

            category = "Software" if stage == "software_install" else "Hardware"

            return [
                SlotSet("short_description", f"Unsuccessful request - {user_query}"),
                SlotSet(
                    "ticket_description",
                    f"User request:\n{user_query}\n\n"
                    "The automated installation did not resolve the issue."
                ),
                SlotSet("category", category),

                SlotSet("awaiting_satisfaction_feedback", None),

                ActiveLoop(None),
                FollowupAction("create_ticket_form")
            ]


class ValidatePrinterInstallForm(FormValidationAction):
    def name(self):
        return "validate_printer_install_form"

    def validate_user_email(self, value, dispatcher, tracker, domain):
        value = (value or "").strip()
        pattern = r"^[\w\.-]+@[\w\.-]+\.\w+$"

        if re.fullmatch(pattern, value):
            return {"user_email": value}

        dispatcher.utter_message(text="That doesn’t look like a valid email. Please enter a correct one.")
        return {"user_email": None}

    def validate_printer_location(self, value, dispatcher, tracker, domain):
        allowed_locations = ["Bangalore", "Mumbai", "Noida"]
        location = (value or "").strip().title()

        if location in allowed_locations:
            return {"printer_location": location}

        dispatcher.utter_message(text=f"Supported locations are: {', '.join(allowed_locations)}.")

        return {"printer_location": None}

class ActionAskPrinterInstallFormSelectedPrinter(Action):
    def name(self):
        return "action_ask_printer_install_form_selected_printer"

    def run(self, dispatcher, tracker, domain):
        location = tracker.get_slot("printer_location")
        printers_data = get_action_list(sync_type=1)

        printers = [
            printer for printer in printers_data
            if printer.get("is_active")
            and printer.get("allow_to_user")
            and location
        ]

        if not printers:
            dispatcher.utter_message(
                "Sorry, I currently support only these locations:\n"
                "Bangalore, Mumbai, Noida.\n\n"
                "Please select one from the options."
            )

            return [SlotSet("printer_location", None)]

        buttons = [
            {
                "title": f"{p.get('driver_id')} — {p.get('printer_displayname') or p.get('printer_name')}",
                "payload": f'/select_printer{{"selected_printer":"{p.get("driver_id")}"}}'
            }
            for p in printers
        ]

        dispatcher.utter_message(
            text="Please select a printer to install:",
            buttons=buttons
        )

        return []

class ActionTriggerPrinterInstallation(Action):
    def name(self):
        return "action_trigger_printer_installation"

    def run(self, dispatcher, tracker, domain):
        printer_id = tracker.get_slot("selected_printer")
        email = tracker.get_slot("user_email")
        location = tracker.get_slot("printer_location")

        if not printer_id or not email or not location:
            dispatcher.utter_message("Missing details to proceed.")
            return []

        printers_data = get_action_list(sync_type=1)

        try:
            printer_id_int = int(printer_id)
        except ValueError:
            dispatcher.utter_message("Invalid printer selected.")
            return [SlotSet("selected_printer", None)]

        selected = next(
            (
                printer for printer in printers_data
                if printer.get("driver_id") == printer_id_int
            ),
            None
        )

        if not selected:
            dispatcher.utter_message("Selected printer is no longer available.")
            return [SlotSet("selected_printer", None)]

        if "@" in email:
            email = email.split("@")[0]

        try:
            schedule_agent_job(
                user_identity=email,
                item_id=str(selected.get("driver_id")),
                action_code="PRT",
                custom_job_name=f"{selected.get('printer_displayname') or selected.get('printer_name')} Installation"
            )
        except Exception:
            dispatcher.utter_message("Failed to schedule printer installation.")
            return []

        printer_name = selected.get("printer_displayname") or selected.get("printer_name")
        dispatcher.utter_message(
            f"The printer '{printer_name}' has been scheduled successfully for installation."
        )

        dispatcher.utter_message(
            "I’ll notify you once it completes. Did this resolve your issue?"
        )

        return [
            SlotSet("awaiting_satisfaction_feedback", "printer_install"),
            SlotSet("user_query", f"Printer installation: {printer_name}"),
            SlotSet("printer_location", None),
            SlotSet("selected_printer", None),
            ActiveLoop(None),
            FollowupAction("action_listen")
        ]

class ActionEndChat(Action):
    def name(self):
        return "action_end_chat"

    def run(self, dispatcher, tracker, domain):
        dispatcher.utter_message(response = "utter_chat_end")
        dispatcher.utter_message(json_message={"type": "CHAT_END"})

        return [
            ActiveLoop(None),
            AllSlotsReset(),
            FollowupAction("action_listen")
        ]

class ActionSessionTimeout(Action):
    def name(self):
        return "action_session_timeout"

    def run(self, dispatcher, tracker, domain):
        print("Ending Chat")
        dispatcher.utter_message(response = "utter_session_timeout")

        return [AllSlotsReset()]

# It should disable chat even after reload

# Ask if anything else to do
# end convo after a certain time period (inactivity)
# not allow new message after ending in same session
# If user wants to see ticket and doesn't know the ID, use their email to fetch ticket numbers and let them choose