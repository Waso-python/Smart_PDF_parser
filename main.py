from langchain.chat_models import GigaChat
from langchain.schema import AIMessage, HumanMessage, SystemMessage
import requests
import uuid
import json
import datetime

ACCESS_TOKEN=""
GIGA_CHAT_CLIENT_ID=""
GIGA_CHAT_AUTH_DATA=""
GIGA_CHAT_SCOPE="GIGACHAT_API_CORP"

def generate_id():
    return str(uuid.uuid4())

def get_creds():
    headers = {
        'Authorization': f'Bearer {GIGA_CHAT_AUTH_DATA}',
        'RqUID': f'{generate_id()}',
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    data = {'scope': f'{GIGA_CHAT_SCOPE}'}
    r = requests.post('', headers=headers, data=data, verify=False)
    json_response =json.loads(r.text)
#     print("get_creds",json_response)
    return json_response

creds = get_creds()
GIGA_ACCESS_TOKEN = creds.get('access_token', '')
GIGA_EXPIRES_AT = creds.get('expires_at', '')

date = datetime.datetime.fromtimestamp(int(GIGA_EXPIRES_AT) // 1000)

print("Access Token истекает - ",date)

def is_token_expired():
    expires_at_timestamp = int(GIGA_EXPIRES_AT)
    current_timestamp = int(datetime.datetime.now().timestamp() * 1000)  # convert to milliseconds
    return current_timestamp >= expires_at_timestamp

def giga_free_answer(question, sys_prompt = "Ты банковский работник, ответь на заданный вопрос максимально лаконично",history = []):
    """Функция для ответа на произвольный вопрос"""
    chat = GigaChat(model="GigaChat-Pro",temperature=0.01, access_token=GIGA_ACCESS_TOKEN, verify_ssl_certs=False)
    messages = [
        SystemMessage(
        content=f'{sys_prompt}'),
        HumanMessage(
        content=question
        ),
        ]
    response = chat(messages).content
    result = response

    return result

question = 'не могу рассчитать риск сегмент'
answer = giga_free_answer(question, sys_prompt)
print(answer)