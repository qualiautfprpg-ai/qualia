#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SISTEMA SIMPLIFICADO: Coletor Climatempo + Publicação a cada 1 minuto
Coleta dados do Climatempo e publica no formato Simepar a cada 60 segundos
"""

import sys
import requests
import json
import time
import re
import logging
from datetime import datetime
from urllib.parse import urljoin
import pytz
from bs4 import BeautifulSoup

# AWS IoT SDK
import AWSIoTPythonSDK.MQTTLib as AWSIoTPYMQTT
import boto3

# =========================
# CONFIGURAÇÃO DE LOGGING
# =========================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/home/ubuntu/climatempo_1min.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# =========================
# CONSTANTES
# =========================
TZ_SP = pytz.timezone("America/Sao_Paulo")
UC_ID = 4000

# Valores de Qo médio mensal (MJ/m²/dia)
QO_MONTHLY_VALUES = {
    1: 17.0, 2: 15.9, 3: 13.9, 4: 11.4, 5: 9.4, 6: 8.1,
    7: 8.7, 8: 10.4, 9: 12.8, 10: 15.0, 11: 16.6, 12: 17.3
}

# =========================
# SCRAPER CLIMATEMPO
# =========================
class ClimatempoScraper:
    def __init__(self):
        self.base_url = 'https://www.climatempo.com.br'
        self.city_url = '/previsao-do-tempo/cidade/1309/doisvizinhos-pr'
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'pt-BR,pt;q=0.9',
        }
    
    def fetch_data(self):
        """Busca dados do Climatempo"""
        try:
            url = urljoin(self.base_url, self.city_url)
            response = requests.get(url, headers=self.headers, timeout=10)
            
            if response.status_code != 200:
                logger.error(f"HTTP {response.status_code}")
                return None
            
            soup = BeautifulSoup(response.content, 'html.parser')
            return self._parse_data(soup)
            
        except Exception as e:
            logger.error(f"Erro fetching: {e}")
            return None
    
    def _parse_data(self, soup):
        """Extrai dados da página"""
        try:
            data = {}
            
            # Extrai dos meta tags
            metas = soup.find_all('meta')
            for meta in metas:
                name = meta.get('name', '')
                content = meta.get('content', '')
                
                if name == 'tmin':
                    data['temp_min'] = float(content) if content else 20.0
                elif name == 'tmax':
                    data['temp_max'] = float(content) if content else 30.0
                elif name == 'chuvamm':
                    data['precipitacao'] = float(content) if content else 0.0
                elif name == 'urmax':
                    data['umidade_max'] = float(content) if content else 70.0
                elif name == 'descricao':
                    data['descricao'] = content
            
            # Se não encontrou nas meta tags, tenta no HTML
            if 'temp_min' not in data:
                temp_elements = soup.find_all('span', {'class': '-gray-light'})
                temps = []
                for elem in temp_elements:
                    text = elem.get_text()
                    if '°' in text:
                        try:
                            temp = float(text.replace('°', '').replace('C', '').strip())
                            temps.append(temp)
                        except:
                            pass
                
                if len(temps) >= 2:
                    data['temp_min'] = min(temps[:2])
                    data['temp_max'] = max(temps[:2])
            
            # Valores padrão se não encontrou
            data.setdefault('temp_min', 20.0)
            data.setdefault('temp_max', 30.0)
            data.setdefault('precipitacao', 0.0)
            data.setdefault('umidade_max', 70.0)
            data.setdefault('umidade_media', 65.0)
            data.setdefault('descricao', 'Tempo estável')
            
            # Calcula umidade média
            if 'umidade_max' in data:
                data['umidade_media'] = data['umidade_max'] * 0.85
            
            logger.info(f"Dados coletados: Tmax={data['temp_max']}°C, Tmin={data['temp_min']}°C")
            return data
            
        except Exception as e:
            logger.error(f"Erro parsing: {e}")
            return None
    
    def calculate_eto(self, temp_max, temp_min, month):
        """Calcula ETo"""
        try:
            qo = QO_MONTHLY_VALUES.get(month, 0.0)
            t_med = (temp_max + temp_min) / 2.0
            delta_t = temp_max - temp_min
            
            if delta_t <= 0:
                return 0.0
            
            eto = 0.0023 * qo * (delta_t ** 0.5) * (t_med + 17.8)
            eto = max(0.0, min(eto, 15.0))
            return round(eto, 2)
            
        except:
            return 0.0

# =========================
# AWS IoT PUBLISHER
# =========================
class AWSIoTPublisher:
    def __init__(self):
        self.client = None
        self.s3_client = None
        self._initialize()
    
    def _initialize(self):
        """Inicializa conexão AWS"""
        try:
            # Configuração AWS
            endpoint = "a1xb4e7ftt8wtn-ats.iot.us-east-1.amazonaws.com"
            client_id = "climatempo_1min"
            
            self.client = AWSIoTPYMQTT.AWSIoTMQTTClient(client_id)
            self.client.configureEndpoint(endpoint, 8883)
            self.client.configureCredentials(
                "/home/ubuntu/aws_iot/AmazonRootCA1.pem",
                "/home/ubuntu/aws_iot/raspgaby.private.key",
                "/home/ubuntu/aws_iot/raspgaby.cert.pem"
            )
            
            # Configurações
            self.client.configureOfflinePublishQueueing(-1)
            self.client.configureDrainingFrequency(2)
            self.client.configureConnectDisconnectTimeout(30)
            self.client.configureMQTTOperationTimeout(30)
            self.client.configureAutoReconnectBackoffTime(1, 32, 30)
            
            # Conecta
            if self.client.connect():
                logger.info("✅ Conectado ao AWS IoT")
            else:
                logger.error("❌ Falha na conexão AWS IoT")
                self.client = None
                
            # S3
            self.s3_client = boto3.client(
                's3',
                aws_access_key_id="COLOQUE_SUA_ACCESS_KEY_AQUI",
                aws_secret_access_key="COLOQUE_SUA_SECRET_KEY_AQUI",
                region_name="us-east-1"
            )
            
        except Exception as e:
            logger.error(f"Erro inicializando AWS: {e}")
            self.client = None
            self.s3_client = None
    
    def publish(self, data):
        """Publica dados no AWS IoT"""
        if not self.client:
            logger.error("Cliente AWS não inicializado")
            return False
        
        try:
            topic = "previsao/simepar"
            message_json = json.dumps(data, ensure_ascii=False)
            
            success = self.client.publish(topic, message_json, 1)
            
            if success:
                logger.info(f"✅ Publicado em '{topic}'")
                # Salva no S3 também
                self._save_to_s3(data)
                return True
            else:
                logger.error("❌ Falha na publicação")
                return False
                
        except Exception as e:
            logger.error(f"Erro publicando: {e}")
            return False
    
    def _save_to_s3(self, data):
        """Salva dados no S3"""
        if not self.s3_client:
            return False
        
        try:
            agora = datetime.now(TZ_SP)
            timestamp = agora.strftime("%Y-%m-%d_%H-%M-%S")
            file_name = f"previsao_simepar/{timestamp}.json"
            
            self.s3_client.put_object(
                Bucket="raspbpibucket",
                Key=file_name,
                Body=json.dumps(data, indent=2).encode('utf-8'),
                ContentType='application/json'
            )
            
            logger.debug(f"💾 Salvo no S3: {file_name}")
            return True
            
        except Exception as e:
            logger.warning(f"Erro S3: {e}")
            return False

# =========================
# PROCESSADOR PRINCIPAL
# =========================
class ClimatempoProcessor:
    def __init__(self):
        self.scraper = ClimatempoScraper()
        self.publisher = AWSIoTPublisher()
        self.running = False
    
    def collect_and_publish(self):
        """Executa uma coleta e publicação"""
        try:
            # Coleta dados
            raw_data = self.scraper.fetch_data()
            if not raw_data:
                logger.error("Falha na coleta de dados")
                return False
            
            # Prepara dados no formato Simepar
            agora = datetime.now(TZ_SP)
            month = agora.month
            
            simepar_data = {
                "UC_id": UC_ID,
                "DataPrevisao": agora.strftime("%d/%m/%Y"),
                "leituraTemperaturaMax": float(raw_data['temp_max']),
                "HorarioTempMax": "14:00",
                "leituraTemperaturaMin": float(raw_data['temp_min']),
                "HorarioTempMin": "06:00",
                "leituraPrecipitacao": float(raw_data['precipitacao']),
                "leituraEto": self.scraper.calculate_eto(
                    raw_data['temp_max'], 
                    raw_data['temp_min'], 
                    month
                ),
                "fonte": "Climatempo",
                "umidade_media_original": float(raw_data['umidade_media']),
                "timestamp_processamento": agora.isoformat(),
                "descricao_tempo": raw_data.get('descricao', ''),
                "url_fonte": "https://www.climatempo.com.br/previsao-do-tempo/cidade/1309/doisvizinhos-pr"
            }
            
            # Publica
            success = self.publisher.publish(simepar_data)
            
            if success:
                logger.info(f"🎯 Dados publicados: {simepar_data['DataPrevisao']} - "
                          f"Tmax={simepar_data['leituraTemperaturaMax']}°C, "
                          f"Tmin={simepar_data['leituraTemperaturaMin']}°C")
            
            return success
            
        except Exception as e:
            logger.error(f"Erro no processamento: {e}")
            return False
    
    def run_continuous(self, interval_seconds=60):
        """
        Executa coletas contínuas a cada X segundos
        
        Args:
            interval_seconds: Intervalo em segundos (padrão: 60 segundos = 1 minuto)
        """
        self.running = True
        
        logger.info("=" * 60)
        logger.info("🚀 INICIANDO PROCESSADOR CLIMATEMPO")
        logger.info(f"📊 Publicando a cada {interval_seconds} segundos")
        logger.info("👂 Pressione Ctrl+C para parar")
        logger.info("=" * 60)
        
        cycle_count = 0
        
        try:
            while self.running:
                cycle_count += 1
                hora_atual = datetime.now(TZ_SP).strftime("%H:%M:%S")
                
                logger.info(f"\n🔄 CICLO #{cycle_count} - {hora_atual}")
                
                # Executa coleta e publicação
                success = self.collect_and_publish()
                
                if not success:
                    logger.warning("⚠️ Coleta falhou, tentando novamente no próximo ciclo")
                
                # Aguarda o intervalo (com contagem regressiva)
                for remaining in range(interval_seconds, 0, -1):
                    if not self.running:
                        break
                    
                    # Mostra progresso a cada 10 segundos
                    if remaining % 10 == 0:
                        logger.debug(f"⏳ Próxima coleta em {remaining}s...")
                    
                    time.sleep(1)
                    
        except KeyboardInterrupt:
            logger.info("\n🛑 Interrompido pelo usuário")
        except Exception as e:
            logger.error(f"💥 Erro fatal: {e}")
        finally:
            self.running = False
            logger.info(f"📊 Total de ciclos executados: {cycle_count}")
            logger.info("👋 Processador encerrado")

# =========================
# FUNÇÃO PRINCIPAL
# =========================
def main():
    """Função principal - executa publicação contínua"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Publica dados do Climatempo no AWS IoT a cada 1 minuto',
        add_help=False
    )
    
    parser.add_argument('--interval', type=int, default=60,
                       help='Intervalo entre coletas em segundos (padrão: 60)')
    parser.add_argument('--once', action='store_true',
                       help='Executar apenas uma vez')
    
    args = parser.parse_args()
    
    # Mostra banner
    print("=" * 60)
    print("🌤️  CLIMATEMPO PROCESSOR - PUBLICAÇÃO CONTÍNUA")
    print(f"⏰ Intervalo: {args.interval} segundos")
    print("📍 Dois Vizinhos - PR")
    print("☁️  Tópico: previsao/simepar")
    print("=" * 60)
    
    # Cria e executa processador
    processor = ClimatempoProcessor()
    
    if args.once:
        logger.info("🔍 Executando coleta única...")
        processor.collect_and_publish()
    else:
        processor.run_continuous(args.interval)

# =========================
# PONTO DE ENTRADA
# =========================
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n🛑 Interrompido pelo usuário")
        sys.exit(0)
    except Exception as e:
        logger.error(f"💥 ERRO FATAL: {e}", exc_info=True)
        sys.exit(1)
