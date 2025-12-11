from langchain_community.chat_models import GigaChat
from langchain.schema import AIMessage, HumanMessage, SystemMessage
import os
import subprocess
import requests
import uuid
import json
import datetime
from dotenv import load_dotenv

load_dotenv()

SYS_PROMPT = "Ты банковский работник"

GIGA_CHAT_AUTH_DATA=os.environ.get("GIGA_ACCESS_KEY")

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
    r = requests.post('https://ngw.devices.sberbank.ru:9443/api/v2/oauth', headers=headers, data=data, verify=False)
    json_response =json.loads(r.text)
    return json_response

def main():
    creds = get_creds()
    print("Ответ от NGW:", creds)

    GIGA_ACCESS_TOKEN = creds.get('access_token')
    GIGA_EXPIRES_AT = creds.get('expires_at')

    if not GIGA_ACCESS_TOKEN:
        print("Токен не получен! Код/сообщение от NGW:", creds.get('code'), creds.get('message'))
        return  # дальше GigaChat вызывать нельзя

    if GIGA_EXPIRES_AT:
        date = datetime.datetime.fromtimestamp(int(GIGA_EXPIRES_AT) // 1000)
        print("Access Token истекает - ", date)
    else:
        print("Поле 'expires_at' отсутствует или пустое, пропускаю расчёт даты")
    
    def giga_free_answer(question, sys_prompt ="Ты банковский работник, ответь на заданный вопрос максимально лаконично",history = []):
        chat = GigaChat(model="GigaChat-Pro",temperature=0.01, access_token=GIGA_ACCESS_TOKEN, verify_ssl_certs=False)
        messages =  [
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
    answer = giga_free_answer(question, SYS_PROMPT)
    print(f"answer - {answer}")

if __name__ == "__main__":
    main()
