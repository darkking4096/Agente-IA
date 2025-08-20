"""
Fernanda IA - Backend Final v5.0
Sistema completo com IA prioritária e banco de dados
"""

import os
import json
import asyncio
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from enum import Enum
from pathlib import Path

from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import httpx
import pandas as pd
from dotenv import load_dotenv
import google.generativeai as genai

# === Configuração ===
load_dotenv()

# Chaves e configurações
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")
EVOLUTION_BASE_URL = os.getenv("EVOLUTION_BASE_URL", "http://localhost:8080")
EVOLUTION_INSTANCE = os.getenv("EVOLUTION_INSTANCE", "instance")
RUN_MODE = os.getenv("RUN_MODE", "dev").lower()
ADMIN_WHATSAPP = os.getenv("ADMIN_WHATSAPP", "")

# Verificar API Key do Gemini
if not GOOGLE_API_KEY:
    raise ValueError("⚠️ GOOGLE_API_KEY não configurada no .env! O sistema precisa da IA para funcionar.")

# Configurar Gemini
genai.configure(api_key=GOOGLE_API_KEY)

# === FastAPI App ===
app = FastAPI(
    title="Fernanda IA",
    description="Sistema inteligente de atendimento com IA e banco de dados",
    version="5.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === Banco de Dados ===
def init_database():
    """Inicializa o banco de dados SQLite"""
    conn = sqlite3.connect('data/fernanda.db')
    cursor = conn.cursor()
    
    # Tabela de pacientes
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Tabela de agendamentos
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            service TEXT NOT NULL,
            specialty TEXT,
            urgency_level INTEGER DEFAULT 0,
            scheduled_date TEXT,
            scheduled_time TEXT,
            status TEXT DEFAULT 'pending',
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            confirmed_at TIMESTAMP,
            FOREIGN KEY (patient_id) REFERENCES patients (id)
        )
    ''')
    
    # Tabela de conversas (histórico)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER,
            user_message TEXT NOT NULL,
            bot_response TEXT NOT NULL,
            intent TEXT,
            state TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (patient_id) REFERENCES patients (id)
        )
    ''')
    
    conn.commit()
    conn.close()

# Criar diretório de dados se não existir
Path("data").mkdir(exist_ok=True)
init_database()

# === Classes de Dados ===
class ConversationState(Enum):
    NEW_CONTACT = "new_contact"
    IDENTIFYING = "identifying"
    COLLECTING_INFO = "collecting_info"
    SCHEDULING = "scheduling"
    CONFIRMING = "confirming"
    COMPLETED = "completed"

class PatientInfo(BaseModel):
    phone: str
    name: Optional[str] = None
    current_issue: Optional[str] = None
    urgency_level: int = 0
    service_needed: Optional[str] = None
    preferred_date: Optional[str] = None
    preferred_time: Optional[str] = None

class ConversationMemory:
    def __init__(self, phone: str):
        self.phone = phone
        self.patient_id: Optional[int] = None
        self.state = ConversationState.NEW_CONTACT
        self.patient_info = PatientInfo(phone=phone)
        self.context_data: Dict[str, Any] = {}
        self.message_count = 0
        self.last_activity = datetime.now()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "phone": self.phone,
            "patient_id": self.patient_id,
            "state": self.state.value,
            "patient_info": self.patient_info.dict(),
            "context_data": self.context_data,
            "message_count": self.message_count
        }

# === Memória em RAM (cache) ===
active_conversations: Dict[str, ConversationMemory] = {}

# === Funções de Banco de Dados ===
def get_or_create_patient(phone: str, name: Optional[str] = None) -> int:
    """Obtém ou cria um paciente no banco"""
    conn = sqlite3.connect('data/fernanda.db')
    cursor = conn.cursor()
    
    # Verificar se existe
    cursor.execute("SELECT id, name FROM patients WHERE phone = ?", (phone,))
    result = cursor.fetchone()
    
    if result:
        patient_id = result[0]
        # Atualizar nome se fornecido e diferente
        if name and name != result[1]:
            cursor.execute(
                "UPDATE patients SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (name, patient_id)
            )
            conn.commit()
    else:
        # Criar novo paciente
        cursor.execute(
            "INSERT INTO patients (phone, name) VALUES (?, ?)",
            (phone, name or "Não informado")
        )
        conn.commit()
        patient_id = cursor.lastrowid
    
    conn.close()
    return patient_id

def save_appointment(memory: ConversationMemory) -> int:
    """Salva um agendamento no banco"""
    conn = sqlite3.connect('data/fernanda.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO appointments (
            patient_id, service, specialty, urgency_level,
            scheduled_date, scheduled_time, status, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        memory.patient_id,
        memory.patient_info.service_needed or "Consulta",
        memory.context_data.get("specialty", "Clínica Geral"),
        memory.patient_info.urgency_level,
        memory.patient_info.preferred_date,
        memory.patient_info.preferred_time,
        "confirmed",
        memory.patient_info.current_issue
    ))
    
    appointment_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return appointment_id

def save_conversation_turn(patient_id: int, user_msg: str, bot_msg: str, state: str):
    """Salva uma interação no histórico"""
    conn = sqlite3.connect('data/fernanda.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO conversations (patient_id, user_message, bot_response, state)
        VALUES (?, ?, ?, ?)
    ''', (patient_id, user_msg[:500], bot_msg[:500], state))
    
    conn.commit()
    conn.close()

def get_patient_history(phone: str) -> List[Dict[str, Any]]:
    """Obtém histórico do paciente"""
    conn = sqlite3.connect('data/fernanda.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT c.user_message, c.bot_response, c.created_at, c.state
        FROM conversations c
        JOIN patients p ON c.patient_id = p.id
        WHERE p.phone = ?
        ORDER BY c.created_at DESC
        LIMIT 10
    ''', (phone,))
    
    history = []
    for row in cursor.fetchall():
        history.append({
            "user": row[0],
            "bot": row[1],
            "timestamp": row[2],
            "state": row[3]
        })
    
    conn.close()
    return history[::-1]  # Reverter para ordem cronológica

# === Carregar Arquivos de Configuração ===
def load_prompt() -> str:
    """Carrega o prompt principal da Fernanda"""
    path = Path("prompt_fernanda.md")
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "Você é Fernanda, assistente virtual acolhedora da clínica odontológica."

def load_clinic_config() -> Dict[str, Any]:
    """Carrega configurações da clínica"""
    path = Path("clinica_config.json")
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"clinic_name": "Clínica Sorriso & Saúde"}

def load_knowledge_base() -> pd.DataFrame:
    """Carrega base de conhecimento"""
    path = Path("knowledge_base.csv")
    if path.exists():
        return pd.read_csv(path)
    # Base padrão
    return pd.DataFrame([
        {"servico": "Triagem de dor", "especialidade": "Endodontia", 
         "palavras_chave": "dor,urgente,emergência", "urgencia": "Alto", "duracao_min": 40},
        {"servico": "Limpeza", "especialidade": "Clínica Geral",
         "palavras_chave": "limpeza,profilaxia", "urgencia": "Baixo", "duracao_min": 45}
    ])

# Carregar configurações
FERNANDA_PROMPT = load_prompt()
CLINIC_CONFIG = load_clinic_config()
KNOWLEDGE_BASE = load_knowledge_base()

# === Extração de Informações ===
def extract_info_from_message(message: str, memory: ConversationMemory) -> Dict[str, Any]:
    """Extrai informações relevantes da mensagem"""
    extracted = {}
    msg_lower = message.lower()
    
    # Extrair nome
    if not memory.patient_info.name:
        # Padrões comuns
        patterns = [
            r'(?:meu nome é|me chamo|sou o?a?)\s+([A-Z][a-zà-ú]+(?:\s+[A-Z][a-zà-ú]+)*)',
            r'^([A-Z][a-zà-ú]+(?:\s+[A-Z][a-zà-ú]+)*)[,.\s]',
        ]
        for pattern in patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                extracted['name'] = match.group(1).strip()
                break
    
    # Extrair telefone
    phone_pattern = r'(?:\+?55\s?)?(?:\(?\d{2}\)?\s?)?\d{4,5}[-\s]?\d{4}'
    phone_match = re.search(phone_pattern, message)
    if phone_match:
        extracted['phone'] = re.sub(r'[^\d]', '', phone_match.group())
    
    # Detectar urgência
    urgency_keywords = {
        10: ['insuportável', 'não aguento', 'emergência'],
        8: ['muita dor', 'bastante dor', 'doendo muito'],
        6: ['dor', 'doendo', 'incômodo'],
        3: ['desconforto', 'sensível']
    }
    
    for level, keywords in urgency_keywords.items():
        if any(kw in msg_lower for kw in keywords):
            extracted['urgency'] = level
            break
    
    # Detectar serviço baseado na knowledge base
    for _, row in KNOWLEDGE_BASE.iterrows():
        keywords = row['palavras_chave'].split(',')
        if any(kw.strip() in msg_lower for kw in keywords):
            extracted['service'] = row['servico']
            extracted['specialty'] = row['especialidade']
            break
    
    # Detectar datas/horários
    if 'hoje' in msg_lower:
        extracted['date_preference'] = 'hoje'
    elif 'amanhã' in msg_lower:
        extracted['date_preference'] = 'amanhã'
    elif 'semana' in msg_lower:
        extracted['date_preference'] = 'esta semana'
    
    # Horários
    time_match = re.search(r'(\d{1,2})[h:](\d{2})?', message)
    if time_match:
        hour = time_match.group(1)
        minute = time_match.group(2) or "00"
        extracted['time_preference'] = f"{hour}:{minute}"
    
        # Intenção explícita de consulta/agendamento
    if any(k in msg_lower for k in ['agendar', 'marcar', 'consulta', 'avaliacao', 'avaliação', 'operar', 'cirurgia']):
        extracted.setdefault('service', 'Consulta')
        memory.context_data['specialty'] = memory.context_data.get('specialty', 'Clínica Geral')

    return extracted

# === Geração de Prompt Contextual ===
def build_intelligent_prompt(memory: ConversationMemory, user_message: str) -> str:
    """Constrói um prompt completo e contextual para o Gemini"""
    
    # Histórico do banco de dados
    db_history = get_patient_history(memory.phone)
    
    # Informações extraídas
    extracted = extract_info_from_message(user_message, memory)
    
    # Atualizar memória com informações extraídas
    if 'name' in extracted:
        memory.patient_info.name = extracted['name']
    if 'urgency' in extracted:
        memory.patient_info.urgency_level = extracted['urgency']
    if 'service' in extracted:
        memory.patient_info.service_needed = extracted['service']
        memory.context_data['specialty'] = extracted.get('specialty', 'Clínica Geral')
    
    # Contexto completo
    context = {
        "estado_atual": memory.state.value,
        "mensagem_numero": memory.message_count + 1,
        "paciente_conhecido": memory.patient_id is not None,
        "informacoes_coletadas": {
            "nome": memory.patient_info.name,
            "telefone": memory.phone,
            "problema": memory.patient_info.current_issue,
            "servico": memory.patient_info.service_needed,
            "urgencia": memory.patient_info.urgency_level,
            "data_preferida": memory.patient_info.preferred_date,
            "horario_preferido": memory.patient_info.preferred_time
        },
        "historico_banco": len(db_history),
        "ultima_visita": db_history[-1]['timestamp'] if db_history else None
    }
    
    # Montar histórico recente
    recent_history = ""
    if db_history:
        # Pegar últimas 3 interações
        for h in db_history[-3:]:
            recent_history += f"Paciente: {h['user']}\nFernanda: {h['bot']}\n"
    
    # Informações que faltam
    missing = []
    if not memory.patient_info.name:
        missing.append("nome completo")
    if not memory.patient_info.service_needed:
        missing.append("motivo específico da consulta")
    if memory.state == ConversationState.SCHEDULING:
        if not memory.patient_info.preferred_date:
            missing.append("data preferida")
        if not memory.patient_info.preferred_time:
            missing.append("horário preferido")
    
    # Prompt estruturado
    prompt = f"""
{FERNANDA_PROMPT}

=== DADOS DA CLÍNICA ===
{json.dumps(CLINIC_CONFIG, ensure_ascii=False, indent=2)}

=== BASE DE CONHECIMENTO ===
Serviços disponíveis:
{KNOWLEDGE_BASE.to_string(index=False)}

=== CONTEXTO DA CONVERSA ===
Estado: {context['estado_atual']}
Mensagem número: {context['mensagem_numero']}
Paciente já conhecido: {'Sim' if context['paciente_conhecido'] else 'Não'}

=== INFORMAÇÕES JÁ COLETADAS ===
{json.dumps(context['informacoes_coletadas'], ensure_ascii=False, indent=2)}

=== HISTÓRICO RECENTE ===
{recent_history if recent_history else "Primeira interação com este paciente"}

=== MENSAGEM ATUAL ===
Paciente: {user_message}

=== INSTRUÇÕES CRÍTICAS ===
1. NUNCA se apresente novamente após a primeira mensagem
2. Se é urgência (dor), seja empática mas RÁPIDA - sugira horário HOJE
3. Use o nome do paciente quando souber
4. Colete apenas UMA informação faltante por vez
5. Seja natural e humana, não robótica
6. Responda em no máximo 2-3 frases
Se o paciente demonstrou intenção de AGENDAR (ex.: "marcar", "agendar", "consulta", "operar", "cirurgia"), vá DIRETO ao agendamento: sugira 2 opções de horário disponíveis (ex.: hoje 14:30 ou 16:00) e peça só a confirmação.
8. Não pergunte sobre tipo de dor (ex.: latejante, pontada, constante) nem peça para “classificar a dor”. Isso NÃO é necessário para agendar.
9. Evite repetir a mesma pergunta em mensagens consecutivas. Se já pediu uma informação nas últimas 2 mensagens, avance com uma sugestão de horário.
10. Informações faltantes: {', '.join(missing) if missing else 'Todas coletadas - pode confirmar agendamento'}
IMPORTANTE: Responda EXATAMENTE como a Fernanda responderia - humana, calorosa, eficiente.
Resposta (máximo 3 frases):"""
    
    return prompt

# === Integração com Gemini ===
async def get_ai_response(prompt: str, temperature: float = 0.7) -> str:
    """Obtém resposta do Gemini com configurações otimizadas"""
    try:
        # Configuração para respostas naturais e concisas
        generation_config = {
            "temperature": temperature,
            "top_p": 0.85,
            "top_k": 40,
            "max_output_tokens": 200,
        }
        
        # Modelo do Gemini
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            generation_config=generation_config,
            safety_settings={
                "HARM_CATEGORY_HARASSMENT": "BLOCK_NONE",
                "HARM_CATEGORY_HATE_SPEECH": "BLOCK_NONE",
                "HARM_CATEGORY_SEXUALLY_EXPLICIT": "BLOCK_NONE",
                "HARM_CATEGORY_DANGEROUS_CONTENT": "BLOCK_NONE",
            }
        )
        
        # Gerar resposta
        response = await asyncio.to_thread(
            model.generate_content,
            prompt
        )
        
        # Extrair texto
        text = response.text.strip()
        
        # Garantir que não seja muito longo
        sentences = text.split('. ')
        if len(sentences) > 3:
            text = '. '.join(sentences[:3]) + '.'
        
        return text
        
    except Exception as e:
        print(f"Erro no Gemini: {e}")
        # Fallback emergencial
        return "Entendi! Me conta um pouco mais para eu poder ajudar você da melhor forma."

# === Evolution API (WhatsApp) ===
async def send_whatsapp_message(phone: str, text: str):
    """Envia mensagem via Evolution API"""
    if RUN_MODE != "prod":
        print(f"[DEV MODE] WhatsApp para {phone}: {text}")
        return
    
    if not all([EVOLUTION_API_KEY, EVOLUTION_BASE_URL, EVOLUTION_INSTANCE]):
        return
    
    url = f"{EVOLUTION_BASE_URL}/message/sendText/{EVOLUTION_INSTANCE}"
    headers = {
        "apikey": EVOLUTION_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "number": phone,
        "text": text
    }
    
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            response = await client.post(url, headers=headers, json=payload)
            print(f"WhatsApp enviado: {response.status_code}")
        except Exception as e:
            print(f"Erro ao enviar WhatsApp: {e}")

# === Processamento Principal ===
async def process_message(phone: str, message: str) -> Dict[str, Any]:
    """Processa uma mensagem e retorna a resposta"""
    
    # Obter ou criar memória da conversa
    if phone not in active_conversations:
        active_conversations[phone] = ConversationMemory(phone)
    
    memory = active_conversations[phone]
    memory.message_count += 1
    memory.last_activity = datetime.now()
    
    # Obter ou criar paciente no banco
    if not memory.patient_id:
        memory.patient_id = get_or_create_patient(phone, memory.patient_info.name)
    
    # Atualizar issue atual
    if not memory.patient_info.current_issue:
        memory.patient_info.current_issue = message[:200]
    
    # Construir prompt inteligente
    prompt = build_intelligent_prompt(memory, message)
    
    # Obter resposta da IA
    ai_response = await get_ai_response(prompt)
    
    # Atualizar estado baseado no contexto
    update_conversation_state(memory, message, ai_response)
    
    # Salvar no banco de dados
    save_conversation_turn(
        memory.patient_id,
        message,
        ai_response,
        memory.state.value
    )
    
    # Se confirmou agendamento, salvar
    if memory.state == ConversationState.COMPLETED:
        appointment_id = save_appointment(memory)
        memory.context_data['appointment_id'] = appointment_id
        
        # Notificar admin se configurado
        if ADMIN_WHATSAPP:
            admin_msg = f"🎯 Novo agendamento confirmado!\n\n"
            admin_msg += f"Paciente: {memory.patient_info.name}\n"
            admin_msg += f"Telefone: {phone}\n"
            admin_msg += f"Serviço: {memory.patient_info.service_needed}\n"
            admin_msg += f"Urgência: {memory.patient_info.urgency_level}/10\n"
            admin_msg += f"ID: #{appointment_id}"
            await send_whatsapp_message(ADMIN_WHATSAPP, admin_msg)
    
    return {
        "phone": phone,
        "message": message,
        "response": ai_response,
        "state": memory.state.value,
        "patient_id": memory.patient_id,
        "appointment_id": memory.context_data.get('appointment_id'),
        "collected_info": memory.patient_info.dict()
    }

def update_conversation_state(memory: ConversationMemory, user_msg: str, bot_response: str):
    """Atualiza o estado da conversa baseado no contexto"""
    msg_lower = user_msg.lower()
    bot_lower = bot_response.lower()

        # Fast-track: se o paciente demonstrou intenção clara de agendar,
    # pule coleta e vá para agendamento.
    booking_words = [
        'agendar', 'marcar', 'agendamento', 'consulta',
        'avaliacao', 'avaliação', 'operar', 'cirurgia',
        'quero pra hoje', 'quero para hoje'
    ]
    if any(w in msg_lower for w in booking_words):
        if memory.state in (
            ConversationState.NEW_CONTACT,
            ConversationState.IDENTIFYING,
            ConversationState.COLLECTING_INFO
        ):
            memory.state = ConversationState.SCHEDULING


    # Lógica de transição de estados
    if memory.state == ConversationState.NEW_CONTACT:
        if memory.patient_info.name:
            memory.state = ConversationState.COLLECTING_INFO
        else:
            memory.state = ConversationState.IDENTIFYING
    
    elif memory.state == ConversationState.IDENTIFYING:
        if memory.patient_info.name:
            memory.state = ConversationState.COLLECTING_INFO
    
    elif memory.state == ConversationState.COLLECTING_INFO:
        # Se tem informações suficientes, mover para agendamento
        if memory.patient_info.name and memory.patient_info.service_needed:
            memory.state = ConversationState.SCHEDULING
    
    elif memory.state == ConversationState.SCHEDULING:
        # Se mencionou data/hora, mover para confirmação
        if memory.patient_info.preferred_date or 'hoje' in msg_lower or 'amanhã' in msg_lower:
            memory.state = ConversationState.CONFIRMING
    
    elif memory.state == ConversationState.CONFIRMING:
        # Palavras de confirmação
        confirm_words = ['sim', 'confirmo', 'pode ser', 'perfeito', 'ok', 'beleza', 'fechado']
        if any(word in msg_lower for word in confirm_words):
            memory.state = ConversationState.COMPLETED

# === Endpoints da API ===
@app.get("/")
async def health_check():
    """Verificação de saúde do sistema"""
    return {
        "status": "online",
        "version": "5.0",
        "clinic": CLINIC_CONFIG.get("clinic_name"),
        "ai": "Gemini 1.5 Flash",
        "database": "SQLite",
        "mode": RUN_MODE
    }

@app.get("/api/status")
async def system_status():
    """Status detalhado do sistema"""
    conn = sqlite3.connect('data/fernanda.db')
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM patients")
    total_patients = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM appointments WHERE status = 'confirmed'")
    total_appointments = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM conversations")
    total_messages = cursor.fetchone()[0]
    
    conn.close()
    
    return {
        "status": "operational",
        "stats": {
            "total_patients": total_patients,
            "confirmed_appointments": total_appointments,
            "total_messages": total_messages,
            "active_conversations": len(active_conversations),
            "knowledge_base_services": len(KNOWLEDGE_BASE)
        },
        "ai_status": "active" if GOOGLE_API_KEY else "missing_api_key",
        "whatsapp_status": "configured" if EVOLUTION_API_KEY else "not_configured"
    }

@app.post("/webhook")
async def webhook_handler(request: Request, x_webhook_token: Optional[str] = Header(None)):
    """Webhook principal para receber mensagens"""
    
    # Validação de token
    if WEBHOOK_TOKEN and x_webhook_token != WEBHOOK_TOKEN:
        return JSONResponse(
            {"error": "Invalid token"},
            status_code=401
        )
    
    # Processar payload
    data = await request.json()
    
    # Extrair informações (suporta múltiplos formatos)
    phone = None
    message = None
    
    # Formato simples (testes)
    if "from" in data and "text" in data:
        phone = str(data["from"])
        message = str(data["text"])
    
    # Formato Evolution API
    elif "data" in data:
        try:
            msg_data = data["data"].get("message", {})
            if not msg_data.get("key", {}).get("fromMe"):
                phone = msg_data.get("key", {}).get("remoteJid", "").split("@")[0]
                msg_content = msg_data.get("message", {})
                
                if "conversation" in msg_content:
                    message = msg_content["conversation"]
                elif "extendedTextMessage" in msg_content:
                    message = msg_content["extendedTextMessage"].get("text", "")
        except Exception as e:
            print(f"Erro ao extrair mensagem Evolution: {e}")
    
    if not phone or not message:
        return {"status": "ignored", "reason": "no_valid_message"}
    
    # Processar mensagem
    result = await process_message(phone, message)
    
    # Enviar resposta via WhatsApp
    await send_whatsapp_message(phone, result["response"])
    
    return {
        "status": "processed",
        "result": result
    }

@app.get("/api/patient/{phone}")
async def get_patient_info(phone: str):
    """Obtém informações de um paciente"""
    conn = sqlite3.connect('data/fernanda.db')
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT p.id, p.name, p.created_at,
               COUNT(DISTINCT a.id) as total_appointments,
               COUNT(DISTINCT c.id) as total_messages
        FROM patients p
        LEFT JOIN appointments a ON p.id = a.patient_id
        LEFT JOIN conversations c ON p.id = c.patient_id
        WHERE p.phone = ?
        GROUP BY p.id
    """, (phone,))
    
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        raise HTTPException(status_code=404, detail="Patient not found")
    
    return {
        "id": result[0],
        "name": result[1],
        "phone": phone,
        "created_at": result[2],
        "total_appointments": result[3],
        "total_messages": result[4],
        "history": get_patient_history(phone)
    }

@app.get("/api/appointments")
async def list_appointments(limit: int = 50):
    """Lista agendamentos recentes"""
    conn = sqlite3.connect('data/fernanda.db')
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT a.id, p.name, p.phone, a.service, a.specialty,
               a.scheduled_date, a.scheduled_time, a.status, a.created_at
        FROM appointments a
        JOIN patients p ON a.patient_id = p.id
        ORDER BY a.created_at DESC
        LIMIT ?
    """, (limit,))
    
    appointments = []
    for row in cursor.fetchall():
        appointments.append({
            "id": row[0],
            "patient_name": row[1],
            "patient_phone": row[2],
            "service": row[3],
            "specialty": row[4],
            "scheduled_date": row[5],
            "scheduled_time": row[6],
            "status": row[7],
            "created_at": row[8]
        })
    
    conn.close()
    return appointments

@app.get("/api/analytics")
async def get_analytics():
    """Analytics do sistema"""
    conn = sqlite3.connect('data/fernanda.db')
    cursor = conn.cursor()
    
    # Estatísticas gerais
    cursor.execute("SELECT COUNT(*) FROM patients")
    total_patients = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM appointments")
    total_appointments = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM appointments WHERE status = 'confirmed'")
    confirmed = cursor.fetchone()[0]
    
    # Taxa de conversão
    conversion_rate = (confirmed / total_appointments * 100) if total_appointments > 0 else 0
    
    # Distribuição por especialidade
    cursor.execute("""
        SELECT specialty, COUNT(*) as count
        FROM appointments
        GROUP BY specialty
        ORDER BY count DESC
    """)
    specialties = {row[0]: row[1] for row in cursor.fetchall()}
    
    # Distribuição por urgência
    cursor.execute("""
        SELECT urgency_level, COUNT(*) as count
        FROM appointments
        GROUP BY urgency_level
        ORDER BY urgency_level DESC
    """)
    urgency_dist = {f"level_{row[0]}": row[1] for row in cursor.fetchall()}
    
    conn.close()
    
    return {
        "overview": {
            "total_patients": total_patients,
            "total_appointments": total_appointments,
            "confirmed_appointments": confirmed,
            "conversion_rate": f"{conversion_rate:.1f}%",
            "active_conversations": len(active_conversations)
        },
        "distributions": {
            "by_specialty": specialties,
            "by_urgency": urgency_dist
        }
    }

@app.post("/api/reset/{phone}")
async def reset_conversation(phone: str):
    """Reseta a conversa de um usuário"""
    if phone in active_conversations:
        del active_conversations[phone]
    return {"status": "reset", "phone": phone}

# === Limpeza periódica ===
async def cleanup_inactive_conversations():
    """Remove conversas inativas da memória"""
    while True:
        await asyncio.sleep(3600)  # A cada hora
        cutoff = datetime.now() - timedelta(hours=6)
        
        to_remove = []
        for phone, memory in active_conversations.items():
            if memory.last_activity < cutoff:
                to_remove.append(phone)
        
        for phone in to_remove:
            del active_conversations[phone]

@app.on_event("startup")
async def startup_event():
    """Inicialização do sistema"""
    print("🚀 Fernanda IA v5.0 iniciando...")
    print(f"✅ Gemini API: {'Configurada' if GOOGLE_API_KEY else '❌ FALTANDO'}")
    print(f"✅ WhatsApp: {'Configurado' if EVOLUTION_API_KEY else '⚠️ Não configurado'}")
    print(f"✅ Modo: {RUN_MODE}")
    
    # Iniciar tarefa de limpeza
    asyncio.create_task(cleanup_inactive_conversations())

@app.on_event("shutdown")
async def shutdown_event():
    """Desligamento do sistema"""
    print("👋 Fernanda IA desligando...")
