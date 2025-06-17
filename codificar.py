import base64

with open("C:/Users/confe/OneDrive/Documentos/ChatBot_Telegram/BotTelegram/google-creds.json", "r") as f:
    content = f.read()

cred_base64 = base64.b64encode(content.encode()).decode()
print(cred_base64)
