import json, os
from dotenv import load_dotenv
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from rasa_sdk import Action
from rasa_sdk.events import SlotSet, FollowupAction
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
        else:
            ticket_id = result.get("number", result.get("request_number"))
            dispatcher.utter_message(f"Your ticket has been created with ticket Id - {ticket_id}")

        return [SlotSet("user_email", None), SlotSet("short_description", None), SlotSet("ticket_description", None), SlotSet("category", None)]


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
        incident_state_number = latest_incident.get("incident_state", "Unknown")
        incident_status = incident_state_mapping.get(incident_state_number, "Unknown")
        return {"ticket_id": incident_number, "description": incident_description, "status": incident_status}
    else:
        return {"error": f"Error fetching the ticket with Id {ticket_id}"}

def fetch_ticket_by_email(user_email):
    incident_state_mapping = {
            "1": "New",
            "2": "In Progress",
            "3": "On Hold",
            "4": "Closed"
        }

    url = f"https://{instance}.service-now.com/api/now/table/sys_user?sysparm_query=email={user_email}"
    response = requests.get(url, auth=HTTPBasicAuth(username,password), headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        if "result" in data:
            sys_id = data["result"][0]["sys_id"]
            incidents_url = f"https://{instance}.service-now.com/api/now/table/incident?sysparm_query=caller_id={sys_id}&sysparm_orderby=sys_created_onDESC"
            incidents_response = requests.get(incidents_url, auth=HTTPBasicAuth(username,password), headers=headers)

            if incidents_response.status_code == 200:
                incidents_data = incidents_response.json()

                if "result" in incidents_data:
                    latest_incident = incidents_data["result"][0]
                    incident_number = latest_incident["number"]
                    incident_description = latest_incident.get("description", "No description available")
                    incident_state_number = latest_incident.get("incident_state", "Unknown")
                    incident_status = incident_state_mapping.get(incident_state_number, "Unknown")
                    return {"ticket_id": incident_number, "description": incident_description, "status": incident_status}
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
        ticket_id = tracker.get_slot("ticket_id")
        user_email = tracker.get_slot("user_email")

        incident_id_regex = r"\bINC\d{6,}\b"
        email_id_regex = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
        
        incident_id_match = re.search(incident_id_regex, ticket_id_or_email)
        events = []
        if incident_id_match:
            ticket_id = incident_id_match.group(0)
            # tracker.slots["ticket_id"] = ticket_id
            events.append(SlotSet("ticket_id", ticket_id))

        email_match = re.search(email_id_regex, ticket_id_or_email)
        if email_match:
            user_email = email_match.group(0)
            # tracker.slots["user_email"] = user_email
            events.append(SlotSet("user_email", user_email))

        if ticket_id:
            result = fetch_ticket_by_id(ticket_id)
            if "error" in result:
                dispatcher.utter_message(f"Failed to fetch the ticket: {result['error']}")
            else:
                dispatcher.utter_message(f"Details of ticket {ticket_id} : \n")
                ticket_id = result.get("ticket_id")
                description = result.get("description")
                status = result.get("status")
                dispatcher.utter_message(f"Ticket Id - {ticket_id}\nDescription - {description}\nStatus - {status}")
                return [SlotSet("ticket_type",None), SlotSet("ticket_id",None), SlotSet("user_email",None)]
            return [SlotSet("ticket_type",None), SlotSet("ticket_id",None), SlotSet("user_email",None)]
        elif user_email:
            result = fetch_ticket_by_email(user_email)
            if "error" in result:
                dispatcher.utter_message(f"Failed to fetch the ticket : {result['error']}")
            else:
                if result:
                    dispatcher.utter_message(f"Latest ticket associated with email {user_email} : \n")
                    ticket_id = result.get("ticket_id")
                    description = result.get("description")
                    status = result.get("status")
                    dispatcher.utter_message(f"Ticket Id - {ticket_id}\nDescription - {description}\nStatus - {status}")
                else:
                    dispatcher.utter_message(f"No tickets found for email {user_email}")
        else:
            dispatcher.utter_message("Please provide either a ticket ID or an email Id to fetch the tickets.")
        
        events.extend([
                    SlotSet("ticket_id_or_email", None),
                    SlotSet("ticket_id", None),
                    SlotSet("user_email", None),
                ])

        return events        

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
        ticket_id = tracker.get_slot("ticket_id_update")
        new_description = tracker.get_slot("new_description")

        if not ticket_id or not new_description:
            dispatcher.utter_message("Please provide the ticket ID, ticket type (incident or service_request), and a new description to update.")
            return [SlotSet("ticket_id",None), SlotSet("new_description",None)]
        
        result = update_ticket_description(ticket_id, new_description)

        if "error" in result:
            dispatcher.utter_message(f"Failed to update the ticket : {result['error']}")
        else:
            ticket_id = result.get("ticket_id")
            dispatcher.utter_message(text=f"Ticket ID {ticket_id} has been updated with the new description")
            SlotSet("ticket_id_update", None)
        
        return [SlotSet("ticket_id_update",None), SlotSet("new_description",None)]

def update_ticket_status(ticket_id, new_status):
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
        "resolved": "5",
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
            "resolved_by": username
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
        ticket_id = tracker.get_slot("ticket_id_update")
        new_status = tracker.get_slot("new_status")

        if not ticket_id or not new_status:
            dispatcher.utter_message("Please provide the ticket ID, and the new status (resolved or canceled).")
            return [SlotSet("new_status", None)]

        result = update_ticket_status(ticket_id, new_status)

        if "error" in result:
            dispatcher.utter_message(f"Failed to update the ticket status: {result['error']}")
            SlotSet("ticket_id_update", None)
            SlotSet("new_status", None)
        else:
            ticket_id = result.get("ticket_id")
            dispatcher.utter_message(text=f"Ticket ID {ticket_id} has been updated to {new_status}.")
        
        return [SlotSet("ticket_id_update", None), SlotSet("new_status", None)]

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

    incidents_url = f"https://{instance}.service-now.com/api/now/table/incident?sysparm_query=caller_id={sys_id}&sysparm_orderby=sys_created_onDESC&sysparm_limit={num_tickets}"
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
        
        result = fetch_user_tickets(user_email)

        if "error" in result:
            dispatcher.utter_message(f"Failed to fetch tickets: {result['error']}")
        else:
            if not result:
                dispatcher.utter_message(f"No tickets found for the user with Email ID {user_email}")
            else:
                dispatcher.utter_message(f"Here are your last {len(result)} tickets: \n")
                for ticket in result:
                    ticket_id = ticket.get("ticket_id")
                    description = ticket.get("description")
                    status = ticket.get("status")
                    dispatcher.utter_message(f"Ticket ID: {ticket_id}\nDescription: {description}\nStatus: {status}\n")
        return [SlotSet("user_email",None), SlotSet("num_tickets",None)]


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
            dispatcher.utter_message('Are you happy with the solution?')
        except Exception as e:
            dispatcher.utter_message("There was an error with the HR API request")
            print(f"Error: {e}")
        
        # return [SlotSet("hr_query_completed", True)]
        return [
            SlotSet("hr_query_completed", True),
            SlotSet("awaiting_satisfaction_feedback", True)
        ]

class ActionHandleUserSatisfaction(Action):
    def name(self):
        return "action_handle_user_satisfaction"
    
    def run(self, dispatcher, tracker, domain):
        user_satisfaction = tracker.latest_message.get("text").lower()

        if user_satisfaction == "yes":
            dispatcher.utter_message("Great! I'm glad I could help. Let me know if you need anything else.")
            return [SlotSet("hr_query_completed", None), SlotSet("we_query_completed",None)]
        elif user_satisfaction == "no":
            dispatcher.utter_message("Sorry to hear that! I'll raise a ticket for you right away.")
            return [FollowupAction("create_ticket_form"), SlotSet("hr_query_completed", None), SlotSet("we_query_completed",None), SlotSet("awaiting_satisfaction_feedback", None)]

        else:
            dispatcher.utter_message("I'm sorry, I didn't understand. Please type 'yes' if you're satisfied or 'no' if you'd like to raise a ticket.")
            return []

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
            dispatcher.utter_message('\n \nAre you happy with the solution?')
        except Exception as e:
            dispatcher.utter_message("There was an error with the WE API request")
            print(f"Error: {e}")
        
        return [SlotSet("we_query_completed",True)]

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

        response_lines = ["I found these relevant troubleshooters:"]
        for match in matches:
            response_lines.append(f"- {match['name']}")
    
        dispatcher.utter_message("\n".join(response_lines))
        dispatcher.utter_message("Did this solution work for you?")

        return [
            SlotSet("troubleshooter_query_completed", True),
            SlotSet("user_query", user_query),
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
        user_text = tracker.latest_message.get("text", "").lower()
        stage = tracker.get_slot("awaiting_satisfaction_feedback")

        if user_text not in ["yes", "no"]:
            dispatcher.utter_message("Please reply with yes or no.")
            return [FollowupAction("action_listen")]

        if stage == "ts_not_found":
            if user_text == "yes":
                dispatcher.utter_message(
                    "I'll raise a ticket for you."
                )
                return [
                    SlotSet("awaiting_satisfaction_feedback", None),
                    SlotSet("troubleshooter_query_completed", None),
                    FollowupAction("create_ticket_form")
                ]

            if user_text == "no":
                dispatcher.utter_message(
                    "Alright. Let me know if you need anything else."
                )
                return [
                    SlotSet("awaiting_satisfaction_feedback", None),
                    SlotSet("troubleshooter_query_completed", None),
                    FollowupAction("action_listen")
                ]

        if stage == "ts_list":
            if user_text == "yes":
                dispatcher.utter_message(
                    "Great! Let me know if you need anything else."
                )
                return [
                    SlotSet("awaiting_satisfaction_feedback", None),
                    SlotSet("troubleshooter_query_completed", None),
                    FollowupAction("action_listen")
                ]

            if user_text == "no":
                return [
                    SlotSet("awaiting_satisfaction_feedback", None),
                    FollowupAction("action_get_troubleshooter_sop")
                ]

        if stage == "ts_sop":
            if user_text == "yes":
                dispatcher.utter_message(
                    "Glad that helped! Let me know if you need anything else."
                )
                return [
                    SlotSet("awaiting_satisfaction_feedback", None),
                    SlotSet("troubleshooter_query_completed", None),
                    FollowupAction("action_listen")
                ]

            if user_text == "no":
                dispatcher.utter_message(
                    "I’ll raise a ticket for you."
                )
                return [
                    SlotSet("awaiting_satisfaction_feedback", None),
                    SlotSet("troubleshooter_query_completed", None),
                    FollowupAction("create_ticket_form")
                ]

        dispatcher.utter_message("Let’s start again.")
        return [
            SlotSet("awaiting_satisfaction_feedback", None),
            FollowupAction("action_listen")
        ]
