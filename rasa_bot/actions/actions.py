import json, os
from dotenv import load_dotenv
from datetime import datetime, timedelta
from rapidfuzz import process, fuzz
from rasa_sdk import Action
from rasa_sdk.events import SlotSet, FollowupAction, ActiveLoop, AllSlotsReset, ReminderScheduled
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
    url = f"https://{instance}.service-now.com/api/now/table/incident"

    url_sys_id = f"https://{instance}.service-now.com/api/now/table/sys_user?sysparm_query=email={user_email}"
    response_sys_id = requests.get(url_sys_id, auth=HTTPBasicAuth(username, password), headers=headers)
    sys_id = None

    if response_sys_id.status_code == 200:
        data = response_sys_id.json()
        if "result" in data and len(data["result"]) > 0:
            sys_id = data["result"][0]["sys_id"]
        else:
            return {"error": f"No user found with Email Id {user_email}"}

    data = {
        "caller_id" : sys_id,
        "short_description": short_description,
        "description": ticket_description,
        "category": category
    }

    response = requests.post(url, auth = HTTPBasicAuth(username,password), headers=headers, json=data)
    
    if response.status_code == 201:
        ticket_data = response.json()['result']
        return ticket_data
    else:
        return {"error": f"Error creating incident ticket : {response.text}"}

class ActionCreateTicket(Action):
    def name(self):
        return "action_create_ticket"

    def run(self, dispatcher, tracker, domain):
        user_email = tracker.get_slot("user_email")
        short_description = tracker.get_slot("short_description")
        ticket_description = tracker.get_slot("ticket_description")
        category = tracker.get_slot("category")

        result = create_incident_ticket(user_email, short_description, ticket_description, category)

        if "error" in result:
            dispatcher.utter_message(f"Failed to create the ticket : {result['error']}")
            return [SlotSet("user_email", None)]
        else:
            ticket_id = result.get("number", result.get("request_number"))
            dispatcher.utter_message(f"Your ticket has been created with ticket Id - {ticket_id}")

        dispatcher.utter_message("Is there anything else I can help you with?")
        return [SlotSet("short_description", None), SlotSet("ticket_description", None), SlotSet("category", None)]

def fetch_ticket_by_id(ticket_id):  
    incident_state_mapping = {
            "1": "New",
            "2": "In Progress",
            "3": "On Hold",
            "4": "Closed"
        }
    
    url = f"https://{instance}.service-now.com/api/now/table/incident?sysparm_query=number={ticket_id}"
    response = requests.get(url, auth = HTTPBasicAuth(username,password), headers=headers)

    if response.status_code == 200:
        data = response.json()
        if not data.get("result"):
            return {"error": f"No ticket found with ID - {ticket_id}"}
        
        latest_incident = data["result"][0]
        incident_number = latest_incident["number"]
        incident_description = latest_incident.get("description", "No description available")
        incident_short_description = latest_incident.get("short_description", "No short description available")
        incident_state_number = latest_incident.get("incident_state", "Unknown")
        incident_status = incident_state_mapping.get(incident_state_number, "Unknown")
        return {"ticket_id": incident_number, "short_description": incident_short_description, "description": incident_description, "status": incident_status}
    else:
        return {"error": f"Error fetching the ticket with Id {ticket_id}"}

def fetch_ticket_by_email(user_email):
    incident_state_mapping = {
        "1": "New",
        "2": "In Progress",
        "3": "On Hold",
        "6": "Resolved",
        "7": "Closed"
    }

    url = f"https://{instance}.service-now.com/api/now/table/sys_user?sysparm_query=email={user_email}"
    response = requests.get(url, auth=HTTPBasicAuth(username,password), headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        if "result" in data:
            sys_id = data["result"][0]["sys_id"]
            incidents_url = (
                f"https://{instance}.service-now.com/api/now/table/incident"
                f"?sysparm_query=caller_id={sys_id}^ORDERBYDESCsys_updated_on"
                f"&sysparm_limit=1"
            )
            incidents_response = requests.get(incidents_url, auth=HTTPBasicAuth(username,password), headers=headers)

            if incidents_response.status_code == 200:
                incidents_data = incidents_response.json()

                if "result" in incidents_data:
                    latest_incident = incidents_data["result"][0]
                    incident_number = latest_incident["number"]
                    incident_description = latest_incident.get("description", "No description available")
                    incident_short_description = latest_incident.get("short_description", "No short description available")
                    incident_state_number = latest_incident.get("incident_state", "Unknown")
                    incident_status = incident_state_mapping.get(incident_state_number, "Unknown")
                    return {"ticket_id": incident_number, "short_description": incident_short_description, "description": incident_description, "status": incident_status}
                else:
                    return {"error": f"No incidents found for the email {user_email}"}
            else:
                return {"error": "Error while fetching incidents"}
        else:
            return {"error": f"No user found with the email {user_email}"}
    else:
        return {"error": "Error fetching user data"}

class ActionFetchTicket(Action):
    def name(self):
        return "action_fetch_ticket"

    def run(self, dispatcher, tracker, domain):
        ticket_id_or_email = tracker.get_slot("ticket_id_or_email")
        ticket_id = None
        user_email = None

        events = []

        if ticket_id_or_email:
            incident_id_regex = r"\bINC\d{6,}\b"
            email_id_regex = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"

            incident_id_match = re.search(incident_id_regex, ticket_id_or_email)
            if incident_id_match:
                ticket_id = incident_id_match.group(0)
                events.append(SlotSet("ticket_id", ticket_id))

            email_match = re.search(email_id_regex, ticket_id_or_email)
            if email_match:
                user_email = email_match.group(0)
                events.append(SlotSet("user_email", user_email))

        if ticket_id:
            result = fetch_ticket_by_id(ticket_id)
            if "error" in result:
                dispatcher.utter_message(f"Failed to fetch the ticket: {result['error']}")
            else:
                dispatcher.utter_message(f"Details of ticket {ticket_id}:\n")
                dispatcher.utter_message(
                    f"Ticket Id - {result.get('ticket_id')}\n"
                    f"Short Description - {result.get('short_description')}\n"
                    f"Description - {result.get('description')}\n"
                    f"Status - {result.get('status')}"
                )
                dispatcher.utter_message("Is there anything else I can help you with?")

        elif user_email:
            result = fetch_ticket_by_email(user_email)
            if "error" in result:
                dispatcher.utter_message(f"Failed to fetch the ticket: {result['error']}")
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
                
                dispatcher.utter_message("Is there anything else I can help you with?")

        else:
            dispatcher.utter_message(
                "Please provide either a ticket ID or an email ID to fetch the tickets."
            )

        events.extend([
            SlotSet("ticket_type", None),
            SlotSet("ticket_id_or_email", None),
            SlotSet("ticket_id", None),
            SlotSet("user_email", None),
        ])

        return events

def get_tickets_by_email(user_email):
    url = (
        f"https://{instance}.service-now.com/api/now/table/incident"
        f"?sysparm_query=caller_id.email={user_email}^incident_stateNOT IN6,7^ORDERBYDESCsys_created_on"
        f"&sysparm_fields=number,short_description,incident_state"
    )

    response = requests.get(
        url,
        auth=HTTPBasicAuth(username, password),
        headers=headers
    )

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

        buttons = [
            {
                "title": f"{t.get('number')} | {(t.get('short_description') or '')[:40]} | {incident_state_mapping.get(str(t.get('incident_state')), 'Unknown')}",
                "payload": t.get("number")
            }
            for t in tickets
        ]

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
        
        buttons = [
            {
                "title": f"{t.get('number')} | {(t.get('short_description') or '')[:40]} | {incident_state_mapping.get(str(t.get('incident_state')), 'Unknown')}",
                "payload": t.get("number")
            }
            for t in tickets
        ]

        dispatcher.utter_message(
            text="Please select the ticket you want to update:",
            buttons=buttons
        )

        return []

def update_ticket_description(ticket_id, new_description):   
    sys_id = None
    
    url_sys_id = f"https://{instance}.service-now.com/api/now/table/incident?sysparm_query=number={ticket_id}"
    response_sys_id = requests.get(url_sys_id, auth=HTTPBasicAuth(username,password), headers=headers)

    if response_sys_id.status_code == 200:
        data_sys_id = response_sys_id.json()
        if "result" in data_sys_id and len(data_sys_id["result"]) > 0:
            sys_id = data_sys_id["result"][0]["sys_id"]

    if not sys_id:
        return {"error": f"No incident found with ID {ticket_id}"}

    url = f"https://{instance}.service-now.com/api/now/table/incident/{sys_id}"

    data = {
        "description": new_description
    }

    response = requests.put(url, auth=HTTPBasicAuth(username, password), headers=headers, json=data)

    if response.status_code == 200:
        data = response.json()
        if "result" in data:
            updated_incident = data["result"]
            incident_number = updated_incident.get("number", "Unknown incident number")
            return {"ticket_id": incident_number, "description": new_description, "status": "Updated Sucessfully"}
        else:
            return {"error": "No result returned from the API"}
    else:
        return {"error": f"Error updating ticket with ID {ticket_id}"}

class ActionUpdateTicketDescription(Action):
    def name(self):
        return "action_update_ticket_description"

    def run(self, dispatcher, tracker, domain):
        user_email = tracker.get_slot("user_email")
        ticket_id = tracker.get_slot("ticket_id_update")
        new_description = tracker.get_slot("new_description")

        if not user_email or not ticket_id or not new_description:
            dispatcher.utter_message("Please provide your email ID, ticket ID and a new description.")
            return [
                SlotSet("user_email", None),
                SlotSet("ticket_id_update", None),
                SlotSet("new_description", None),
            ]
        
        result = update_ticket_description(ticket_id, new_description)

        if "error" in result:
            dispatcher.utter_message(f"Failed to update the ticket : {result['error']}")
            dispatcher.utter_message("Is there anything else I can help you with?")
            return [
                SlotSet("user_email", None),
                SlotSet("ticket_id_update", None),
                SlotSet("new_description", None),
            ]
        else:
            ticket_id = result.get("ticket_id")
            dispatcher.utter_message(text=f"Ticket ID {ticket_id} has been updated with the new description")
            dispatcher.utter_message("Is there anything else I can help you with?")
        
        return [
            SlotSet("ticket_id_update", None),
            SlotSet("new_description", None),
        ]

def update_ticket_status(ticket_id, new_status):
    if not new_status:
        return {"error": "Missing status."}

    new_status = new_status.lower().strip()

    if new_status in ["resolve", "resolved"]:
        new_status = "resolved"
    elif new_status in ["close", "closed"]:
        new_status = "closed"

    sys_id = None
    url_sys_id = f"https://{instance}.service-now.com/api/now/table/incident?sysparm_query=number={ticket_id}"
    response_sys_id = requests.get(
        url_sys_id,
        auth=HTTPBasicAuth(username, password),
        headers=headers
    )

    if response_sys_id.status_code == 200:
        data_sys_id = response_sys_id.json()
        if data_sys_id.get("result"):
            sys_id = data_sys_id["result"][0]["sys_id"]

    if not sys_id:
        return {"error": f"No incident found with ID {ticket_id}"}

    status_map = {
        "resolved": "6",
        "closed": "7"
    }

    if new_status not in status_map:
        return {"error": "Only resolve or close is allowed."}

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

    response = requests.patch(
        url,
        auth=HTTPBasicAuth(username, password),
        headers=headers,
        json=data
    )

    if response.status_code == 200:
        result = response.json().get("result", {})
        return {
            "ticket_id": result.get("number", ticket_id),
            "status": new_status,
            "status_update": "Successfully updated"
        }

    return {
        "error": f"ServiceNow error ({response.status_code}): {response.text}"
    }

class ActionUpdateTicketStatus(Action):
    def name(self):
        return "action_update_ticket_status"

    def run(self, dispatcher, tracker, domain):
        user_email = tracker.get_slot("user_email")
        ticket_id = tracker.get_slot("ticket_id_update")
        new_status = tracker.get_slot("new_status")

        if not user_email or not ticket_id or not new_status:
            dispatcher.utter_message("Please provide your email ID, ticket ID and the new status.")
            return [
                SlotSet("user_email", None),
                SlotSet("ticket_id_update", None),
                SlotSet("new_status", None),
            ]

        result = update_ticket_status(ticket_id, new_status)

        if "error" in result:
            dispatcher.utter_message(f"Failed to update the ticket status: {result['error']}")
            dispatcher.utter_message("Is there anything else I can help you with?")
            return [
                SlotSet("user_email", None),
                SlotSet("ticket_id_update", None),
                SlotSet("new_status", None),
            ]
        else:
            ticket_id = result.get("ticket_id")
            dispatcher.utter_message(text=f"Ticket ID {ticket_id} has been updated to {new_status}.")
            dispatcher.utter_message("Is there anything else I can help you with?")
        
        return [
            SlotSet("ticket_id_update", None),
            SlotSet("new_status", None),
        ]

def fetch_user_tickets(user_email, num_tickets = 5):    
    url_sys_id = f"https://{instance}.service-now.com/api/now/table/sys_user?sysparm_query=email={user_email}"
    response_sys_id = requests.get(url_sys_id, auth=HTTPBasicAuth(username, password), headers=headers)

    if response_sys_id.status_code == 200:
        data = response_sys_id.json()
        if "result" in data and len(data["result"]) > 0:
            sys_id = data["result"][0]["sys_id"]
        else:
            return {"error": f"No user found with Email Id {user_email}"}
    else:
        return {"error": "Error fetching user data"}
    
    incident_state_mapping = {
            "1": "New",
            "2": "In Progress",
            "3": "On Hold",
            "4": "Closed"
        }

    incidents_url = (
        f"https://{instance}.service-now.com/api/now/table/incident"
        f"?sysparm_query=caller_id={sys_id}^ORDERBYDESCsys_created_on"
        f"&sysparm_limit={num_tickets}"
    )

    incidents_response = requests.get(incidents_url, auth=HTTPBasicAuth(username, password), headers=headers)

    if incidents_response.status_code == 200:
        incidents_data = incidents_response.json()
        if "result" in incidents_data and len(incidents_data["result"]) > 0:
            tickets = []
            for incident in incidents_data["result"]:
                incident_number = incident["number"]
                incident_description = incident.get("description", "No description available")
                incident_state_number = incident.get("incident_state", "Unknown")
                incident_status = incident_state_mapping.get(incident_state_number, "Unknown")
                
                tickets.append({
                    "ticket_id": incident_number,
                    "description": incident_description,
                    "status": incident_status
                })
            return tickets
        else:
            return {"error": f"No incidents found for user with email {user_email}"}
    else:
        return {"error": "Error fetching incidents"}

class ActionFetchLastTickets(Action):
    def name(self):
        return "action_fetch_last_tickets"

    def run(self, dispatcher, tracker, domain):
        user_email = tracker.get_slot("user_email")
        num_tickets = tracker.get_slot("num_tickets") or 5
        
        result = fetch_user_tickets(user_email, int(num_tickets))

        if "error" in result:
            dispatcher.utter_message(f"Failed to fetch tickets: {result['error']}")
            return [SlotSet("user_email",None), SlotSet("num_tickets",None)]
        else:
            if not result:
                dispatcher.utter_message(f"No tickets found for the user with Email ID {user_email}")
                return [SlotSet("user_email",None), SlotSet("num_tickets",None)]
            else:
                dispatcher.utter_message(f"Here are your last {len(result)} tickets: \n")
                for ticket in result:
                    ticket_id = ticket.get("ticket_id")
                    description = ticket.get("description")
                    status = ticket.get("status")
                    dispatcher.utter_message(f"Ticket ID: {ticket_id}\nDescription: {description}\nStatus: {status}\n")
        
        dispatcher.utter_message("Is there anything else I can help you with?")
        return [SlotSet("num_tickets",None)]

class ActionGetHRResponse(Action):
    def name(self):
        return "action_get_hr_response"
    
    def run(self, dispatcher, tracker, domain):
        user_query = tracker.latest_message.get("text")
        domain_name = os.getenv("PROGRESSIVE_DOMAIN")
        BEARER_TOKEN = os.getenv("BEARER_TOKEN")
        
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
            "Authorization": f"Bearer {BEARER_TOKEN}",
            "Content-Type": "application/json"
        }

        try:
            response = requests.request("POST", url, json=payload, headers=headers)
            response_data = response.json()
            raw_answer = response_data.get("answer", "")
            clean_answer = re.search(r'</think>(.*)', raw_answer, re.DOTALL)
            dispatcher.utter_message(clean_answer.group(1).strip())
            dispatcher.utter_message('HR - Are you happy with the solution?')
        except Exception as e:
            dispatcher.utter_message("There was an error with the HR API request")
            print(f"Error: {e}")
        
        return [
            SlotSet("hr_query_completed", True),
            SlotSet("awaiting_satisfaction_feedback", "hr"),
            SlotSet("user_query", user_query)
        ]

class ActionHandleUserSatisfaction(Action):
    def name(self):
        return "action_handle_user_satisfaction"

    def run(self, dispatcher, tracker, domain):
        text = tracker.latest_message.get("text", "").lower().strip()
        user_query = tracker.get_slot("user_query") or "HR / WorkElevate query"

        positive_phrases = [
            "yes", "yeah", "yup", "sure", "that helped", "it helped", "this helped",
            "yes it worked", "that worked", "looks good", "all good", "satisfied",
            "i'm satisfied", "resolved", "problem solved"
        ]

        negative_phrases = [
            "no", "nope", "not really", "no thanks", "it didn't help",
            "that didn't help", "it didn't", "this didn't work", "it didn't work",
            "not helpful", "still not working", "not resolved",
            "i'm not satisfied", "no it did not", "this didn't solve my problem"
        ]

        is_positive = any(p in text for p in positive_phrases)
        is_negative = any(n in text for n in negative_phrases)

        if is_positive and not is_negative:
            dispatcher.utter_message(
                "Great! I'm glad I could help. Let me know if you need anything else."
            )
            return [
                SlotSet("hr_query_completed", None),
                SlotSet("we_query_completed", None),
                SlotSet("awaiting_satisfaction_feedback", None),
                ActiveLoop(None),
                FollowupAction("action_listen")
            ]

        if is_negative:
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

        dispatcher.utter_message(
            "I'm sorry, I didn't understand. Please type 'yes' if you're satisfied "
            "or 'no' if you'd like to raise a ticket."
        )
        return [FollowupAction("action_listen")]

class ActionGetWorkElevateResponse(Action):
    def name(self):
        return "action_get_workelevate_response"
    
    def run(self, dispatcher, tracker, domain):
        user_query = tracker.latest_message.get("text")
        domain_name = os.getenv("WORKELEVATE_DOMAIN")
        BEARER_TOKEN = os.getenv("BEARER_TOKEN")

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
            "Authorization": f"Bearer {BEARER_TOKEN}",
            "Content-Type": "application/json"
        }

        try:
            response = requests.request("POST", url, json=payload, headers=headers)
            response_data = response.json()
            raw_answer = response_data.get("answer", "")
            clean_answer = re.search(r'</think>(.*)', raw_answer, re.DOTALL)
            dispatcher.utter_message(clean_answer.group(1).strip())
            dispatcher.utter_message('\n \nWE - Are you happy with the solution?')
        except Exception as e:
            dispatcher.utter_message("There was an error with the WE API request")
            print(f"Error: {e}")
        
        return [
            SlotSet("we_query_completed",True),
            SlotSet("awaiting_satisfaction_feedback", "we"), 
            SlotSet("user_query", user_query),
            ReminderScheduled(
                intent_name="EXTERNAL_inactivity_timeout",
                trigger_date_time=datetime.now() + timedelta(seconds=30),
                name="inactivity_timeout",
                kill_on_user_message=True
            )
        ]

class ActionFallback(Action):
    def name(self):
        return "action_default_fallback"

    def run(self, dispatcher, tracker, domain):
        dispatcher.utter_message("Sorry, I don't understand that. Can you please rephrase or ask something related to HR policies, WorkElevate or any issue you are facing?")
        return []

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
                timeout=3
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
            dispatcher.utter_message(
                "I couldn't find a matching troubleshooter. "
                "Would you like me to create a ticket?"
            )
            return [
                SlotSet("troubleshooter_query_completed", True),
                SlotSet("awaiting_satisfaction_feedback", "ts_not_found"),
                FollowupAction("action_listen")
            ]

        buttons = []
        for match in matches:
            name = match["name"]
            buttons.append({
                "title": name,
                "payload": f'/select_troubleshooter{{"selected_troubleshooter":"{name}"}}'
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
            dispatcher.utter_message(
                "Got it. I’ll look for a more detailed solution instead."
            )
            return [
                SlotSet("awaiting_satisfaction_feedback", None),
                FollowupAction("action_get_troubleshooter_sop")
            ]

        dispatcher.utter_message(f"I’ve run the troubleshooter: {t}.")
        dispatcher.utter_message("Did this solution work for you?")

        return [
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
            response = requests.request("POST", url, json=payload, headers=headers)
            response_data = response.json()
            raw_answer = response_data.get("answer", "")
            clean_answer = re.search(r'</think>(.*)', raw_answer, re.DOTALL)
            dispatcher.utter_message(clean_answer.group(1).strip())

            dispatcher.utter_message("Did this solution work for you?")
        except Exception as e:
            dispatcher.utter_message(f"There was an error with fetching the SOP. Error - {e}")
        return [
            SlotSet("awaiting_satisfaction_feedback", "ts_sop"),
            FollowupAction("action_listen")
        ]

class ActionHandleUserSatisfactionTroubleShooter(Action):
    def name(self):
        return "action_handle_user_satisfaction_troubleshooter"

    def run(self, dispatcher, tracker, domain):
        text = tracker.latest_message.get("text", "").lower().strip()
        stage = tracker.get_slot("awaiting_satisfaction_feedback")
        user_query = tracker.get_slot("user_query") or "Technical issue"

        positive_phrases = [
            "yes", "yeah", "yup", "sure", "that helped", "it helped", "this helped",
            "yes it worked", "that worked", "looks good", "all good", "satisfied",
            "i'm satisfied", "resolved", "problem solved"
        ]

        negative_phrases = [
            "no", "nope", "not really", "no thanks", "it didn't help",
            "that didn't help", "it didn't", "this didn't work", "it didn't work",
            "not helpful", "still not working", "not resolved",
            "i'm not satisfied", "no it did not", "this didn't solve my problem"
        ]

        is_positive = any(p in text for p in positive_phrases)
        is_negative = any(n in text for n in negative_phrases)

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

class ActionHandleSoftwareRequest(Action):
    def name(self):
        return "action_handle_software_request"

    def run(self, dispatcher, tracker, domain):
        software_query = tracker.get_slot("software_name")
        confirmed_software = tracker.get_slot("confirmed_software_name")

        if confirmed_software:
            software_name = confirmed_software
            software_info = SOFTWARES[software_name]

            events = [
                SlotSet("software_name", None)
            ]

            if software_info.get("is_blacklisted"):
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

            if software_info.get("is_restricted") or software_info.get("license_type") == "licensed":
                dispatcher.utter_message(
                    f"{software_name.title()} requires approval before installation.\n"
                    "I’ll raise a request for approval."
                )

                return events + [
                    SlotSet("short_description", f"Software request: {software_name.title()}"),
                    SlotSet(
                        "ticket_description",
                        f"User requested installation of {software_name.title()}.\n"
                        f"Source: {software_info.get('source')}\n"
                        f"License type: {software_info.get('license_type')}\n"
                        f"Approval required."
                    ),
                    SlotSet("category", "Software"),
                    ActiveLoop(None),
                    FollowupAction("create_ticket_form")
                ]

            dispatcher.utter_message(
                f"{software_name.title()} installation has been triggered successfully."
            )

            dispatcher.utter_message(
                "Is there anything else I can help you with?"
            )

            return events + [
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

        matches = resolve_software_matches(software_query.lower())

        if not matches:
            dispatcher.utter_message(
                "I couldn’t find that software in our approved catalog. I’ll raise a ticket for you."
            )

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

        if software_info.get("is_blacklisted"):
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

        if software_info.get("is_restricted") or software_info.get("license_type") == "licensed":
            dispatcher.utter_message(
                f"{software_name.title()} requires approval before installation.\n"
                "I’ll raise a request for approval."
            )

            return events + [
                SlotSet("short_description", f"Software request: {software_name.title()}"),
                SlotSet(
                    "ticket_description",
                    f"User requested installation of {software_name.title()}.\n"
                    f"Source: {software_info.get('source')}\n"
                    f"License type: {software_info.get('license_type')}\n"
                    f"Approval required."
                ),
                SlotSet("category", "Software"),
                ActiveLoop(None),
                FollowupAction("create_ticket_form")
            ]

        dispatcher.utter_message(
            f"{software_name.title()} installation has been triggered successfully."
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

with open("softwares.json", "r", encoding="utf-8") as f:
    SOFTWARES = json.load(f)

def resolve_software_matches(software_name: str, limit: int = 5, threshold: int = 85):
    matches = process.extract(software_name, SOFTWARES.keys(), limit=limit, scorer = fuzz.partial_ratio)

    results = []
    for best_match, score, _ in matches:
        if score >= threshold:
            results.append((best_match, SOFTWARES[best_match]))

    return results

class ActionListPrintersByLocation(Action):
    def name(self):
        return "action_list_printers_by_location"

    def run(self, dispatcher, tracker, domain):
        location = tracker.get_slot("printer_location")

        if not location:
            dispatcher.utter_message("Please select a location.")
            return []

        with open("printers.json", "r", encoding="utf-8") as f:
            printers_data = json.load(f)

        location_key = location.strip().lower()
        printers = printers_data.get(location_key)

        if not printers:
            dispatcher.utter_message(
                "Sorry, I currently support only these locations:\n"
                "Bangalore, Mumbai, Noida.\n\n"
                "Please select one from the options."
            )
            return [
                SlotSet("printer_location", None)
            ]

        buttons = [
            {
                "title": f"{p['id']} — {p['name']}",
                "payload": f'/select_printer{{"selected_printer":"{p["id"]}"}}'
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
        printer = tracker.get_slot("selected_printer")
        email = tracker.get_slot("user_email")
        location = tracker.get_slot("printer_location")

        if not printer or not email or not location:
            dispatcher.utter_message("Missing details to proceed.")
            return []

        dispatcher.utter_message(
            "Printer installation has been triggered successfully."
        )
        dispatcher.utter_message(
            "Is there anything else I can help you with?"
        )

        return [
            SlotSet("printer_location", None),
            SlotSet("selected_printer", None)
        ]

class ActionEndChat(Action):
    def name(self):
        return "action_end_chat"

    def run(self, dispatcher, tracker, domain):
        print("Session Ended")
        dispatcher.utter_message("Thanks for chatting! Have a great day. 👋")

        return [
            ActiveLoop(None),
            AllSlotsReset(),
            FollowupAction("action_listen")
        ]


# Ask if anything else to do
# end convo after a certain time period (inactivity)
# end convo button
# not allow new message after ending in same session
# If user wants to see ticket and doesn't know the ID, use their email to fetch ticket numbers and let them choose