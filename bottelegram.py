import os
import json
import base64
import tempfile
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

from langchain_community.vectorstores import FAISS
from langchain_openai import ChatOpenAI, OpenAIEmbeddings  # ✅ Cambio aquí
from langchain.chains import RetrievalQA

from langchain_community.document_loaders import (
    UnstructuredPDFLoader,
    UnstructuredWordDocumentLoader,
    UnstructuredPowerPointLoader,
)
from langchain.text_splitter import RecursiveCharacterTextSplitter

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# --------------------------------
# CONFIGURACIÓN
# --------------------------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
GOOGLE_CREDS = os.getenv("GOOGLE_CREDS")  # Contenido base64 del JSON

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --------------------------------
# FUNCIONES PARA GOOGLE DRIVE
# --------------------------------
def get_documents_from_drive(folder_id):
    creds_json = base64.b64decode(GOOGLE_CREDS).decode("utf-8")
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict)
    service = build('drive', 'v3', credentials=creds)

    query = f"'{folder_id}' in parents and trashed = false"
    results = service.files().list(q=query, fields="files(id, name, mimeType)").execute()
    files = results.get("files", [])
    documents = []

    for file in files:
        file_id = file["id"]
        file_name = file["name"]
        mime_type = file["mimeType"]

        try:
            # Exportar archivos de Google Docs/Slides
            if mime_type == "application/vnd.google-apps.document":
                request = service.files().export_media(fileId=file_id,
                    mimeType="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
                extension = ".docx"
            elif mime_type == "application/vnd.google-apps.presentation":
                request = service.files().export_media(fileId=file_id,
                    mimeType="application/vnd.openxmlformats-officedocument.presentationml.presentation")
                extension = ".pptx"
            elif mime_type == "application/vnd.google-apps.spreadsheet":
                logger.warning(f"📄 Archivo de hoja de cálculo omitido: {file_name}")
                continue
            else:
                request = service.files().get_media(fileId=file_id)
                extension = os.path.splitext(file_name)[-1]

            fh = tempfile.NamedTemporaryFile(delete=False, suffix=extension)
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()
            fh.flush()

            # Cargar con el loader adecuado
            if extension == ".pdf":
                loader = UnstructuredPDFLoader(fh.name)
            elif extension == ".docx":
                loader = UnstructuredWordDocumentLoader(fh.name)
            elif extension == ".pptx":
                loader = UnstructuredPowerPointLoader(fh.name)
            else:
                logger.warning(f"📄 Tipo de archivo no compatible: {file_name}")
                continue

            docs = loader.load()
            documents.extend(docs)

        except Exception as e:
            logger.error(f"❌ Error al procesar {file_name}: {e}")

    return documents

# --------------------------------
# INICIO DEL BOT
# --------------------------------
def main():
    logger.info("🔍 Cargando documentos desde Google Drive...")
    documents = get_documents_from_drive(DRIVE_FOLDER_ID)
    logger.info(f"📄 Total de documentos cargados: {len(documents)}")

    if not documents:
        logger.error("❌ No se pudo cargar ningún documento válido. El bot no puede continuar.")
        return

    # Dividir texto en fragmentos
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
    texts = text_splitter.split_documents(documents)

    if not texts:
        logger.error("❌ No se pudo dividir el contenido en fragmentos.")
        return

    embedding = OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY)
    vectorstore = FAISS.from_documents(texts, embedding)

    qa_chain = RetrievalQA.from_chain_type(
        llm=ChatOpenAI(openai_api_key=OPENAI_API_KEY),
        retriever=vectorstore.as_retriever()
    )

    # Comandos del bot
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "👋 Hola, soy tu bot académico. Pregúntame sobre cualquier tema relacionado con los documentos cargados."
        )

    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_question = update.message.text
        logger.info(f"🧠 Pregunta recibida: {user_question}")
        try:
            result = await qa_chain.ainvoke({"query": user_question})
            response_text = result.get("result", "No encontré información.")
            await update.message.reply_text(response_text)
        except Exception as e:
            logger.error(f"❌ Error al responder: {e}")
            await update.message.reply_text("Ocurrió un error al procesar tu pregunta.")

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("✅ Bot iniciado correctamente.")
    app.run_polling()

if __name__ == "__main__":
    main()
