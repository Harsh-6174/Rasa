import requests, json

url = "http://10.60.0.125:5005/webhooks/rest/webhook"

payload = {
    "message": "",
    "sender": "98cab441-03fa-47b4-800d-d16d7d03dc47"
}
headers = {
    "Accept": "*/*",
    "Content-Type": "application/json",
    "Referer": ""
}

response = requests.request("POST", url, json=payload, headers=headers)
json_response = json.dumps(response.json(), indent=4)
print(json_response)