import streamlit as st
import requests
import os
import json
st.set_page_config(page_title="ExplicaSurf", page_icon="🌊")
# --- Configurações e Chaves de API ---
# ATENÇÃO: Chaves de API hardcodificadas APENAS para depuração. Em produção, use variáveis de ambiente ou um arquivo .env
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
# Coordenadas de Stella Maris, Salvador/BA
LATITUDE = -12.9681
LONGITUDE = -38.3519
# --- Funções para Coleta de Dados ---
@st.cache_data(ttl=3600) # Cacheia os dados por 1 hora
def get_weather_data(lat, lon, api_key):
    url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={api_key}&units=metric&lang=pt_br"
    try:
        response = requests.get(url)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"Erro ao buscar dados do OpenWeatherMap: {e}")
        return None
@st.cache_data(ttl=3600) # Cacheia os dados por 1 hora
def get_marine_data(lat, lon):
    # Restaurando todas as variáveis necessárias para a interpretação da IA
    variables = "swell_wave_height,swell_period,swell_direction,wave_direction,wind_speed,wind_direction"
    url = f"https://marine-api.open-meteo.com/v1/marine?latitude={lat}&longitude={lon}&hourly={variables}&forecast_days=1"
    try:
        response = requests.get(url)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"Erro ao buscar dados do Open-Meteo Marine: {e}")
        return None
# --- Função para Preparação dos Dados para a IA ---
def prepare_data_for_ia(weather_data, marine_data):
    if not weather_data or not marine_data:
        return None
    current_temp = weather_data["main"]["temp"]
    weather_desc = weather_data["weather"][0]["description"]
    wind_speed_ms = weather_data["wind"]["speed"]
    wind_deg = weather_data["wind"]["deg"]
    # Para dados marinhos, pegar o valor mais recente (primeira hora da previsão)
    swell_height = marine_data["hourly"]["swell_wave_height"][0]
    swell_period = marine_data["hourly"]["swell_period"][0]
    swell_direction = marine_data["hourly"]["swell_direction"][0]
    wave_direction = marine_data["hourly"]["wave_direction"][0]
    marine_wind_speed = marine_data["hourly"]["wind_speed"][0]
    marine_wind_direction = marine_data["hourly"]["wind_direction"][0]
    # Converter velocidade do vento de m/s para km/h
    wind_speed_kmh = wind_speed_ms * 3.6
    # Calcular uma métrica de energia da onda (simplificada)
    # Energia ~ Altura^2 * Período (aproximação)
    wave_energy = (swell_height**2) * swell_period
    data_summary = f"""
    Dados Climáticos Atuais para Stella Maris:
    Temperatura: {current_temp}°C
    Condição: {weather_desc}
    Velocidade do Vento: {wind_speed_kmh:.1f} km/h
    Direção do Vento: {wind_deg}° (graus)
    Previsão Oceânica para Stella Maris (Próximas Horas):
    Altura da Onda (Swell): {swell_height:.1f} metros
    Período da Onda (Swell): {swell_period:.1f} segundos
    Direção do Swell: {swell_direction:.1f}° (graus)
    Direção da Onda: {wave_direction:.1f}° (graus)
    Velocidade do Vento (Marinho): {marine_wind_speed:.1f} km/h
    Direção do Vento (Marinho): {marine_wind_direction:.1f}° (graus)
    Energia Estimada da Onda: {wave_energy:.2f} (unidade arbitrária)
    """
    return data_summary
# --- Função para Obter Interpretação da IA ---
def get_ia_interpretation(data_summary, user_level, openai_api_key):
    if not data_summary or not openai_api_key:
        return "Dados insuficientes para gerar interpretação da IA."
    # Definir o tom e o foco com base no nível do usuário
    if user_level == "iniciante":
        level_prompt = "Foque em segurança, se é um bom dia para aprender, e o básico (onda pequena/grande, vento forte/fraco, correnteza)."
        tone_prompt = "Use uma linguagem muito simples e encorajadora."
    elif user_level == "intermediario":
        level_prompt = "Explique um pouco mais sobre a formação da onda, influência do vento, e como as condições afetam a performance."
        tone_prompt = "Use uma linguagem amigável e didática, com alguns termos técnicos."
    else: # avancado
        level_prompt = "Detalhe nuances do swell, como a energia da onda afeta a quebra, e como as condições se comparam a dias épicos. Mencione a influência da direção do swell em Stella Maris (Leste puxa para direita, Sul/Sudeste puxa para esquerda)."
        tone_prompt = "Use uma linguagem técnica, mas acessível, com termos específicos do surf."
    prompt = f"""
    Você é um especialista em surf e seu objetivo é traduzir dados técnicos de previsão de mar e clima para surfistas. 
    Com base nos seguintes dados de previsão para a praia de Stella Maris, Salvador/BA, forneça uma explicação clara e acessível para um surfista de nível {user_level}. 
    {level_prompt}
    {tone_prompt}
    
    Considere as seguintes particularidades de Stella Maris:
    - Swell de Leste: Ondulação puxa para a direita (sentido Salvador).
    - Swell de Sul/Sudeste: Ondulação puxa para a esquerda (sentido Ipitanga).
    - Outono/Inverno: Mais vento, mais correnteza, mar mais forte.
    - Verão/Primavera: Menos correnteza, mais dias com pouco vento, mas pode ter ondas grandes.
    
    Dados de Previsão:
    {data_summary}
    Interpretação para Surfista {user_level}:
    """
    headers = {
        "Authorization": f"Bearer {openai_api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "gpt-3.5-turbo", # Você pode experimentar com outros modelos como "gpt-4" se tiver acesso
        "messages": [
            {"role": "system", "content": "Você é um assistente útil e experiente em surf, focado em segurança e clareza."},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 500,
        "temperature": 0.7 # Controla a criatividade da resposta (0.0 a 1.0)
    }
    
    try:
        response = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, data=json.dumps(payload))
        response.raise_for_status()
        ia_response = response.json()
        return ia_response["choices"][0]["message"]["content"]
    except requests.exceptions.RequestException as e:
        st.error(f"Erro ao chamar a API da OpenAI: {e}")
        return "Não foi possível obter a interpretação da IA no momento."
# --- Interface Streamlit ---
st.title("🌊 ExplicaSurf: Seu Guia de Previsão de Surf")
st.write("Bem-vindo ao ExplicaSurf! Aqui você terá as previsões de surf traduzidas para uma linguagem que você entende, focando em Stella Maris.")
# Adicionando exibição das chaves de API na interface para depuração

# Seleção do nível do surfista
user_level = st.selectbox(
    "Qual o seu nível de surfista?",
    ("iniciante", "intermediario", "avancado"),
    index=0 # Padrão para iniciante
)
if st.button("Buscar Previsão e Interpretar"): # Botão para buscar e interpretar
    # As chaves agora estão hardcodificadas, então não precisamos mais verificar os os.getenv
    with st.spinner("Buscando dados e interpretando com a IA..."):
        weather_data = get_weather_data(LATITUDE, LONGITUDE, OPENWEATHER_API_KEY)
        marine_data = get_marine_data(LATITUDE, LONGITUDE)
        if weather_data and marine_data:
            st.subheader("Dados Técnicos Atuais para Stella Maris")
            st.json(weather_data) # Mostra os dados brutos do clima
            st.json(marine_data) # Mostra os dados brutos do mar
            data_for_ia = prepare_data_for_ia(weather_data, marine_data)
            if data_for_ia:
                st.subheader(f"Interpretação da IA para Surfista {user_level.capitalize()}")
                interpretation = get_ia_interpretation(data_for_ia, user_level, OPENAI_API_KEY)
                st.info(interpretation)
            else:
                st.error("Não foi possível preparar os dados para a IA.")
        else:
            st.error("Não foi possível obter todos os dados de previsão.")
st.markdown("---")



