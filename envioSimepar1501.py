import AWSIoTPythonSDK.MQTTLib as AWSIoTPYMQTT
import boto3
import json
import time
import random
import logging
import pytz
import threading
from datetime import datetime, timedelta
from typing import Dict, Optional, Callable
from dataclasses import dataclass
from enum import Enum

# =========================
# Configuração de Logging
# =========================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    handlers=[
        logging.FileHandler('/home/ubuntu/weather_processor.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# =========================
# Constantes e Enums
# =========================
TZ_SP = pytz.timezone("America/Sao_Paulo")
UC_ID = 4000

# Valores de Qo médio mensal (MJ/m²/dia)
QO_MONTHLY_VALUES = {
    1: 17.0, 2: 15.9, 3: 13.9, 4: 11.4, 5: 9.4, 6: 8.1,
    7: 8.7, 8: 10.4, 9: 12.8, 10: 15.0, 11: 16.6, 12: 17.3
}

class ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"

@dataclass
class ConnectionStats:
    """Estatísticas de conexão"""
    connection_attempts: int = 0
    successful_connections: int = 0
    failed_connections: int = 0
    messages_sent: int = 0
    messages_failed: int = 0
    last_connection_time: Optional[datetime] = None
    last_message_time: Optional[datetime] = None

# =========================
# Configuração AWS
# =========================
AWS_CONFIG = {
    'endpoint': "a1xb4e7ftt8wtn-ats.iot.us-east-1.amazonaws.com",
    'client_id': "rasp_processor",
    'cert_paths': {
        'root_ca': "/home/ubuntu/aws_iot/AmazonRootCA1.pem",
        'certificate': "/home/ubuntu/aws_iot/raspgaby.cert.pem",
        'private_key': "/home/ubuntu/aws_iot/raspgaby.private.key"
    },
    's3': {
        'access_key': "COLOQUE_SUA_ACCESS_KEY_AQUI",
        'secret_key': "COLOQUE_SUA_SECRET_KEY_AQUI",
        'bucket': "raspbpibucket",
        'region': "us-east-1"
    },
    'mqtt': {
        'timeout': 30,  # Timeout aumentado para 30 segundos
        'keep_alive': 60,
        'reconnect_base': 1,
        'reconnect_max': 32,
        'reconnect_max_time': 30,
        'max_operation_retries': 3
    }
}

# =========================
# Cálculos Meteorológicos
# =========================
class WeatherCalculator:
    """Classe para cálculos meteorológicos"""
    
    @staticmethod
    def calculate_eto(max_temp: float, min_temp: float, month: int) -> float:
        """
        Calcula ETo usando fórmula de Hargreaves simplificada.
        """
        try:
            # Obtém Qo para o mês
            qo = QO_MONTHLY_VALUES.get(month, 0.0)
            if qo == 0.0:
                logger.warning(f"Qo não encontrado para mês {month}")
                return 0.0
            
            # Calcula temperatura média
            t_med = (max_temp + min_temp) / 2.0
            delta_t = max_temp - min_temp
            
            # Verifica se delta_t é válido
            if delta_t <= 0:
                logger.warning(f"Delta T <= 0: Tmax={max_temp}, Tmin={min_temp}")
                return 0.0
            
            # Fórmula de Hargreaves simplificada
            eto = 0.0023 * qo * (delta_t ** 0.5) * (t_med + 17.8)
            
            # Limita valores extremos
            eto = max(0.0, min(eto, 15.0))
            
            logger.info(f"ETo calculado: {eto:.2f} mm/dia (Qo={qo}, Tmax={max_temp}, Tmin={min_temp})")
            return eto
            
        except Exception as e:
            logger.error(f"Erro ao calcular ETo: {e}")
            return 0.0

# =========================
# Processador de Dados
# =========================
class DataProcessor:
    """Processa dados do PlugField e converte para formato Simepar"""
    
    @staticmethod
    def convert_plugfield_to_simepar(plugfield_data: Dict) -> Dict:
        """
        Converte dados do formato PlugField para formato Simepar.
        """
        try:
            # Extrai dados do PlugField
            data_str = plugfield_data.get("data", "")
            precipitacao = plugfield_data.get("precipitacao_mm", 0.0)
            temp_max = plugfield_data.get("temp_max", 0.0)
            temp_min = plugfield_data.get("temp_min", 0.0)
            umidade_media = plugfield_data.get("umidade_media", 0.0)
            
            # Converte data de YYYY-MM-DD para DD/MM/YYYY
            try:
                data_obj = datetime.strptime(data_str, "%Y-%m-%d")
                data_formatada = data_obj.strftime("%d/%m/%Y")
                month = data_obj.month
            except:
                # Se falhar, usa data atual
                agora_sp = datetime.now(TZ_SP)
                data_formatada = agora_sp.strftime("%d/%m/%Y")
                month = agora_sp.month
            
            # Calcula ETo
            eto = WeatherCalculator.calculate_eto(temp_max, temp_min, month)
            
            # Horários estimados (padrão climático)
            horario_temp_max = "14:00"
            horario_temp_min = "06:00"
            
            # Monta mensagem no formato Simepar
            simepar_message = {
                "UC_id": UC_ID,
                "DataPrevisao": data_formatada,
                "leituraTemperaturaMax": float(temp_max),
                "HorarioTempMax": horario_temp_max,
                "leituraTemperaturaMin": float(temp_min),
                "HorarioTempMin": horario_temp_min,
                "leituraPrecipitacao": float(precipitacao),
                "leituraEto": float(eto),
                "fonte": "PlugField",
                "umidade_media_original": float(umidade_media),
                "timestamp_processamento": datetime.now(TZ_SP).isoformat()
            }
            
            logger.info(f"Dados convertidos: Tmax={temp_max}°C, Tmin={temp_min}°C, "
                       f"Precip={precipitacao}mm, ETo={eto:.2f}mm")
            
            return simepar_message
            
        except Exception as e:
            logger.error(f"Erro ao converter dados PlugField: {e}")
            return None

# =========================
# Connection Health Monitor
# =========================
class ConnectionHealthMonitor:
    """Monitora a saúde da conexão"""
    
    def __init__(self):
        self.last_ping_time = None
        self.ping_interval = 300  # 5 minutos
        self.connection_quality = 1.0  # 0.0 a 1.0
        
    def record_ping(self):
        """Registra um ping bem-sucedido"""
        self.last_ping_time = datetime.now()
        self.connection_quality = min(1.0, self.connection_quality + 0.1)
        
    def record_failure(self):
        """Registra uma falha"""
        self.connection_quality = max(0.0, self.connection_quality - 0.2)
        
    def should_ping(self) -> bool:
        """Verifica se deve fazer um ping"""
        if self.last_ping_time is None:
            return True
        elapsed = (datetime.now() - self.last_ping_time).total_seconds()
        return elapsed > self.ping_interval
        
    def get_quality(self) -> float:
        """Retorna a qualidade da conexão"""
        return self.connection_quality

# =========================
# AWS IoT Manager Aprimorado
# =========================
class AWSIoTManager:
    """Classe para gerenciar conexão e comunicação com AWS IoT"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.client = None
        self.connection_state = ConnectionState.DISCONNECTED
        self.stats = ConnectionStats()
        self.health_monitor = ConnectionHealthMonitor()
        self.s3_client = None
        self.connection_lock = threading.RLock()
        self._init_s3_client()
        self._init_mqtt_client()
        
    def _init_s3_client(self):
        """Inicializa cliente S3"""
        try:
            self.s3_client = boto3.client(
                's3',
                aws_access_key_id=self.config['s3']['access_key'],
                aws_secret_access_key=self.config['s3']['secret_key'],
                region_name=self.config['s3']['region']
            )
            logger.info("Cliente S3 inicializado com sucesso")
        except Exception as e:
            logger.error(f"Erro ao inicializar cliente S3: {e}")
            self.s3_client = None
            
    def _init_mqtt_client(self):
        """Inicializa cliente MQTT"""
        try:
            self.client = AWSIoTPYMQTT.AWSIoTMQTTClient(self.config['client_id'])
            self.client.configureEndpoint(self.config['endpoint'], 8883)
            self.client.configureCredentials(
                self.config['cert_paths']['root_ca'],
                self.config['cert_paths']['private_key'],
                self.config['cert_paths']['certificate']
            )
            
            # Configurações otimizadas para conexão estável
            mqtt_config = self.config['mqtt']
            self.client.configureOfflinePublishQueueing(-1)  # Fila infinita
            self.client.configureDrainingFrequency(2)  # 2 Hz
            self.client.configureConnectDisconnectTimeout(mqtt_config['timeout'])
            self.client.configureMQTTOperationTimeout(mqtt_config['timeout'])
            self.client.configureAutoReconnectBackoffTime(
                mqtt_config['reconnect_base'],
                mqtt_config['reconnect_max'],
                mqtt_config['reconnect_max_time']
            )
            
            # Configura callbacks
            self.client.on_online = self._on_connection_online
            self.client.on_offline = self._on_connection_offline
            
            logger.info("Cliente MQTT inicializado com sucesso")
            
        except Exception as e:
            logger.error(f"Erro ao inicializar cliente MQTT: {e}")
            self.client = None
            
    def _on_connection_online(self):
        """Callback quando a conexão fica online"""
        with self.connection_lock:
            self.connection_state = ConnectionState.CONNECTED
            self.stats.last_connection_time = datetime.now()
            self.stats.successful_connections += 1
            logger.info("Conexão MQTT estabelecida (callback)")
            
    def _on_connection_offline(self):
        """Callback quando a conexão fica offline"""
        with self.connection_lock:
            if self.connection_state == ConnectionState.CONNECTED:
                self.connection_state = ConnectionState.DISCONNECTED
                logger.warning("Conexão MQTT perdida (callback)")
                
    def connect(self, max_retries: int = 3) -> bool:
        """
        Conecta ao AWS IoT Core com retry automático.
        """
        with self.connection_lock:
            if self.connection_state == ConnectionState.CONNECTED:
                logger.info("Já conectado")
                return True
                
            self.connection_state = ConnectionState.CONNECTING
            retry_delay = 1
            
            for attempt in range(max_retries):
                try:
                    self.stats.connection_attempts += 1
                    logger.info(f"Tentando conexão {attempt+1}/{max_retries}...")
                    
                    # Tenta conexão com timeout
                    result = self._connect_with_timeout(30)
                    
                    if result:
                        self.connection_state = ConnectionState.CONNECTED
                        self.stats.last_connection_time = datetime.now()
                        self.stats.successful_connections += 1
                        self.health_monitor.record_ping()
                        
                        logger.info("✅ Conectado ao AWS IoT Core com sucesso!")
                        self._log_connection_stats()
                        return True
                        
                except Exception as e:
                    logger.error(f"Erro na tentativa {attempt+1}: {e}")
                    self.stats.failed_connections += 1
                    self.health_monitor.record_failure()
                    
                    if attempt < max_retries - 1:
                        logger.info(f"Tentando novamente em {retry_delay} segundos...")
                        time.sleep(retry_delay)
                        retry_delay *= 2  # Backoff exponencial
                        
            self.connection_state = ConnectionState.ERROR
            logger.error("❌ Falha na conexão após todas as tentativas")
            return False
            
    def _connect_with_timeout(self, timeout: int) -> bool:
        """
        Tenta conexão com timeout controlado.
        """
        connect_result = [None]
        
        def do_connect():
            try:
                connect_result[0] = self.client.connect()
            except Exception as e:
                connect_result[0] = e
                
        thread = threading.Thread(target=do_connect)
        thread.daemon = True
        thread.start()
        thread.join(timeout)
        
        if thread.is_alive():
            logger.warning("Timeout na conexão")
            return False
            
        if isinstance(connect_result[0], Exception):
            raise connect_result[0]
            
        return connect_result[0]
        
    def ensure_connected(self) -> bool:
        """
        Garante que está conectado, reconectando se necessário.
        """
        with self.connection_lock:
            if self.connection_state == ConnectionState.CONNECTED:
                # Verifica saúde da conexão
                if self.health_monitor.should_ping():
                    return self._test_connection()
                return True
                
            # Tenta reconectar
            logger.warning("Não conectado, tentando reconectar...")
            return self.connect()
            
    def _test_connection(self) -> bool:
        """
        Testa a conexão publicando uma mensagem de ping.
        """
        try:
            ping_msg = {
                "type": "ping",
                "client_id": self.config['client_id'],
                "timestamp": datetime.now(TZ_SP).isoformat(),
                "stats": {
                    "messages_sent": self.stats.messages_sent,
                    "messages_failed": self.stats.messages_failed
                }
            }
            
            # Publica com QoS 0 e timeout curto
            success = self.client.publish(
                f"$aws/things/{self.config['client_id']}/ping",
                json.dumps(ping_msg),
                0
            )
            
            if success:
                self.health_monitor.record_ping()
                logger.debug("Ping bem-sucedido")
                return True
                
        except Exception as e:
            logger.warning(f"Falha no ping: {e}")
            
        self.health_monitor.record_failure()
        return False
        
    def publish(self, topic: str, message: dict, qos: int = 1, 
                max_retries: int = None) -> bool:
        """
        Publica mensagem no tópico MQTT com retry automático.
        """
        if max_retries is None:
            max_retries = self.config['mqtt']['max_operation_retries']
            
        for attempt in range(max_retries):
            try:
                # Garante conexão antes de publicar
                if not self.ensure_connected():
                    logger.warning(f"Falha ao conectar, tentativa {attempt+1}")
                    time.sleep(2 ** attempt)  # Backoff exponencial
                    continue
                    
                # Serializa mensagem
                message_json = json.dumps(message, ensure_ascii=False)
                
                # Publica com timeout controlado
                publish_result = self._publish_with_timeout(topic, message_json, qos, 15)
                
                if publish_result:
                    self.stats.messages_sent += 1
                    self.stats.last_message_time = datetime.now()
                    logger.info(f"✅ Mensagem publicada em '{topic}' (tentativa {attempt+1})")
                    return True
                    
                logger.warning(f"Publicação falhou, tentativa {attempt+1}")
                
            except Exception as e:
                logger.error(f"Erro na publicação (tentativa {attempt+1}): {e}")
                self.connection_state = ConnectionState.DISCONNECTED
                self.health_monitor.record_failure()
                
            # Backoff antes da próxima tentativa
            if attempt < max_retries - 1:
                delay = 2 ** attempt  # 1, 2, 4, 8, ...
                logger.info(f"Aguardando {delay}s antes da próxima tentativa...")
                time.sleep(delay)
                
        self.stats.messages_failed += 1
        logger.error(f"❌ Falha após {max_retries} tentativas de publicação")
        self._log_connection_stats()
        return False
        
    def _publish_with_timeout(self, topic: str, message: str, 
                             qos: int, timeout: int) -> bool:
        """
        Publica com timeout controlado.
        """
        publish_result = [None]
        publish_error = [None]
        
        def do_publish():
            try:
                publish_result[0] = self.client.publish(topic, message, qos)
            except Exception as e:
                publish_error[0] = e
                
        thread = threading.Thread(target=do_publish)
        thread.daemon = True
        thread.start()
        thread.join(timeout)
        
        if thread.is_alive():
            logger.warning(f"Timeout na publicação para tópico '{topic}'")
            return False
            
        if publish_error[0]:
            raise publish_error[0]
            
        return publish_result[0]
        
    def subscribe(self, topic: str, callback: Callable) -> bool:
        """
        Inscreve-se em um tópico MQTT.
        """
        try:
            if not self.ensure_connected():
                logger.error("Não conectado para inscrever-se")
                return False
                
            self.client.subscribe(topic, 1, callback)
            logger.info(f"Inscrito no tópico '{topic}'")
            return True
            
        except Exception as e:
            logger.error(f"Erro ao se inscrever no tópico '{topic}': {e}")
            return False
            
    def save_to_s3(self, message: dict, folder: str) -> bool:
        """
        Salva dados no S3.
        """
        if not self.s3_client:
            logger.error("Cliente S3 não inicializado")
            return False
            
        try:
            # Data/hora atual no fuso de São Paulo
            agora_sp = datetime.now(TZ_SP)
            timestamp = agora_sp.strftime("%Y-%m-%d_%H-%M-%S")
            file_name = f"{folder}/{timestamp}.json"
            
            # Converte para JSON
            message_json = json.dumps(message, indent=4, ensure_ascii=False)
            
            # Faz upload para S3
            self.s3_client.put_object(
                Bucket=self.config['s3']['bucket'],
                Key=file_name,
                Body=message_json.encode('utf-8'),
                ContentType='application/json; charset=utf-8',
                ContentDisposition=f'inline; filename="{file_name}"'
            )
            
            logger.info(f"💾 Dados salvos no S3: {file_name}")
            return True
            
        except Exception as e:
            logger.error(f"Erro ao salvar no S3: {e}")
            return False
            
    def disconnect(self):
        """Desconecta do AWS IoT Core"""
        with self.connection_lock:
            if self.client and self.connection_state == ConnectionState.CONNECTED:
                try:
                    self.client.disconnect()
                    self.connection_state = ConnectionState.DISCONNECTED
                    logger.info("Desconectado do AWS IoT Core")
                except Exception as e:
                    logger.error(f"Erro ao desconectar: {e}")
                    
    def _log_connection_stats(self):
        """Loga estatísticas de conexão"""
        stats = self.stats
        quality = self.health_monitor.get_quality()
        
        logger.info("=" * 50)
        logger.info("📊 ESTATÍSTICAS DE CONEXÃO")
        logger.info(f"  Tentativas de conexão: {stats.connection_attempts}")
        logger.info(f"  Conexões bem-sucedidas: {stats.successful_connections}")
        logger.info(f"  Conexões falhas: {stats.failed_connections}")
        logger.info(f"  Mensagens enviadas: {stats.messages_sent}")
        logger.info(f"  Mensagens falhas: {stats.messages_failed}")
        logger.info(f"  Qualidade da conexão: {quality:.1%}")
        
        if stats.last_connection_time:
            elapsed = datetime.now() - stats.last_connection_time
            logger.info(f"  Última conexão: {elapsed.total_seconds():.0f}s atrás")
            
        if stats.last_message_time:
            elapsed = datetime.now() - stats.last_message_time
            logger.info(f"  Última mensagem: {elapsed.total_seconds():.0f}s atrás")
            
        logger.info("=" * 50)

# =========================
# Buffer de Mensagens
# =========================
class MessageBuffer:
    """Buffer para armazenar mensagens quando offline"""
    
    def __init__(self, max_size: int = 100):
        self.buffer = []
        self.max_size = max_size
        self.lock = threading.RLock()
        
    def add(self, message: dict, topic: str):
        """Adiciona mensagem ao buffer"""
        with self.lock:
            if len(self.buffer) >= self.max_size:
                self.buffer.pop(0)  # Remove a mais antiga
                
            self.buffer.append({
                'message': message,
                'topic': topic,
                'timestamp': datetime.now(),
                'attempts': 0
            })
            
            logger.info(f"Mensagem adicionada ao buffer. Tamanho: {len(self.buffer)}")
            
    def get_pending_messages(self):
        """Retorna mensagens pendentes"""
        with self.lock:
            return self.buffer.copy()
            
    def remove(self, message_index: int):
        """Remove mensagem do buffer"""
        with self.lock:
            if 0 <= message_index < len(self.buffer):
                self.buffer.pop(message_index)
                
    def clear(self):
        """Limpa o buffer"""
        with self.lock:
            self.buffer.clear()
            logger.info("Buffer limpo")

# =========================
# Aplicação Principal
# =========================
class PlugFieldProcessor:
    """Processa dados do PlugField e converte para formato Simepar"""
    
    def __init__(self):
        self.iot_manager = AWSIoTManager(AWS_CONFIG)
        self.data_processor = DataProcessor()
        self.message_buffer = MessageBuffer()
        self.is_running = False
        
        # Contadores para estatísticas
        self.messages_received = 0
        self.messages_processed = 0
        
        # Thread para processar buffer
        self.buffer_thread = None
        
    def initialize(self) -> bool:
        """
        Inicializa a aplicação.
        """
        logger.info("🚀 Inicializando PlugField Processor...")
        
        # Conecta ao AWS IoT
        if not self.iot_manager.connect():
            logger.error("Falha na inicialização: não foi possível conectar ao AWS IoT")
            return False
            
        # Inicia thread do buffer
        self.buffer_thread = threading.Thread(target=self._process_buffer_loop, daemon=True)
        self.buffer_thread.start()
        
        return True
        
    def _process_buffer_loop(self):
        """Processa mensagens no buffer periodicamente"""
        while self.is_running:
            try:
                # Processa buffer a cada 30 segundos
                time.sleep(30)
                self._process_pending_messages()
            except Exception as e:
                logger.error(f"Erro no processamento do buffer: {e}")
                
    def _process_pending_messages(self):
        """Processa mensagens pendentes no buffer"""
        pending = self.message_buffer.get_pending_messages()
        
        if not pending:
            return
            
        logger.info(f"📨 Processando {len(pending)} mensagens pendentes no buffer...")
        
        for i, item in enumerate(pending):
            try:
                # Tenta publicar a mensagem
                success = self.iot_manager.publish(
                    item['topic'],
                    item['message'],
                    max_retries=2  # Menos tentativas para mensagens do buffer
                )
                
                if success:
                    # Remove do buffer se bem-sucedido
                    self.message_buffer.remove(i - (len(pending) - len(self.message_buffer.get_pending_messages())))
                    logger.info(f"Mensagem do buffer publicada com sucesso")
                    
            except Exception as e:
                logger.error(f"Erro ao processar mensagem do buffer: {e}")
                
    def on_plugfield_message(self, client, userdata, message):
        """
        Callback para mensagens recebidas do tópico PlugField.
        """
        try:
            self.messages_received += 1
            
            # Decodifica a mensagem
            payload_str = message.payload.decode('utf-8')
            logger.info(f"📥 Mensagem recebida [{self.messages_received}]: {payload_str[:200]}...")
            
            # Parseia JSON
            plugfield_data = json.loads(payload_str)
            
            # Verifica se tem os campos necessários
            required_fields = ["data", "precipitacao_mm", "temp_max", "temp_min"]
            if not all(field in plugfield_data for field in required_fields):
                logger.warning(f"Mensagem incompleta. Campos recebidos: {list(plugfield_data.keys())}")
                return
                
            # Converte para formato Simepar
            simepar_message = self.data_processor.convert_plugfield_to_simepar(plugfield_data)
            
            if not simepar_message:
                logger.error("Falha ao converter dados")
                return
                
            # Tenta publicar imediatamente
            if self.iot_manager.publish("previsao/simepar", simepar_message):
                self.messages_processed += 1
                logger.info(f"✅ Mensagem publicada com sucesso [{self.messages_processed}]")
                
                # Salva no S3
                self.iot_manager.save_to_s3(simepar_message, "previsao_simepar_from_plugfield")
                
                # Gera e envia dados de sensores fake
                self._send_sensor_data(simepar_message["DataPrevisao"])
            else:
                # Se falhar, armazena no buffer
                logger.warning("Falha na publicação, armazenando no buffer...")
                self.message_buffer.add(simepar_message, "previsao/simepar")
                
        except json.JSONDecodeError as e:
            logger.error(f"Erro ao decodificar JSON: {e}")
        except Exception as e:
            logger.error(f"Erro no processamento da mensagem: {e}")
            
    def _send_sensor_data(self, forecast_date: str):
        """
        Gera e envia dados de sensores simulados.
        """
        try:
            # Gera dados de sensores simulados
            sensor_data = {
                "UC_id": UC_ID,
                "DataPrevisao": forecast_date,
                "leituraUmidadeSolo": round(random.uniform(30, 80), 2),
                "leituraTemperaturaSolo": round(random.uniform(15, 25), 2),
                "leituraCondutividadeSolo": round(random.uniform(20, 50), 2),
                "leituraTemperaturaAr": round(random.uniform(15, 30), 2),
                "leituraUmidadeAr": round(random.uniform(40, 90), 2),
                "fonte": "simulado",
                "timestamp": datetime.now(TZ_SP).isoformat()
            }
            
            logger.info(f"📡 Dados de sensores gerados")
            
            # Tenta publicar
            if not self.iot_manager.publish("sensores/dados", sensor_data):
                # Se falhar, armazena no buffer
                self.message_buffer.add(sensor_data, "sensores/dados")
                
            # Salva no S3
            self.iot_manager.save_to_s3(sensor_data, "sensores_dados")
                
        except Exception as e:
            logger.error(f"Erro ao enviar dados de sensores: {e}")
            
    def run(self):
        """
        Executa o processador principal.
        """
        if not self.initialize():
            logger.error("Aplicação não pôde ser inicializada. Encerrando.")
            return
            
        self.is_running = True
        
        # Inscreve-se no tópico do PlugField
        logger.info("🔔 Inscrevendo-se no tópico 'plugfield/forecast/daily'...")
        if not self.iot_manager.subscribe("plugfield/forecast/daily", self.on_plugfield_message):
            logger.error("Falha ao se inscrever no tópico. Encerrando.")
            self.shutdown()
            return
            
        logger.info("✅ Aplicação inicializada e pronta!")
        logger.info("📡 Aguardando mensagens do PlugField...")
        logger.info("👂 Pressione Ctrl+C para encerrar")
        
        try:
            # Mantém o programa em execução
            while self.is_running:
                # Log periódico de estatísticas
                if self.messages_received > 0 and self.messages_received % 5 == 0:
                    self._log_runtime_stats()
                    
                time.sleep(1)
                    
        except KeyboardInterrupt:
            logger.info("🛑 Aplicação interrompida pelo usuário")
        except Exception as e:
            logger.error(f"💥 Erro fatal na execução da aplicação: {e}")
        finally:
            self.shutdown()
            
    def _log_runtime_stats(self):
        """Loga estatísticas de runtime"""
        buffer_size = len(self.message_buffer.get_pending_messages())
        connection_quality = self.iot_manager.health_monitor.get_quality()
        
        logger.info("📈 ESTATÍSTICAS DE RUNTIME")
        logger.info(f"  Mensagens recebidas: {self.messages_received}")
        logger.info(f"  Mensagens processadas: {self.messages_processed}")
        logger.info(f"  Mensagens no buffer: {buffer_size}")
        logger.info(f"  Qualidade conexão: {connection_quality:.1%}")
        logger.info(f"  Estado conexão: {self.iot_manager.connection_state.value}")
            
    def shutdown(self):
        """Encerra a aplicação"""
        logger.info("🔄 Encerrando aplicação...")
        self.is_running = False
        
        # Mostra estatísticas finais
        logger.info("📊 ESTATÍSTICAS FINAIS:")
        logger.info(f"  • Mensagens recebidas: {self.messages_received}")
        logger.info(f"  • Mensagens processadas: {self.messages_processed}")
        logger.info(f"  • Mensagens no buffer: {len(self.message_buffer.get_pending_messages())}")
        
        # Loga estatísticas de conexão
        self.iot_manager._log_connection_stats()
        
        # Salva estado do buffer antes de sair
        if len(self.message_buffer.get_pending_messages()) > 0:
            logger.warning(f"⚠️  {len(self.message_buffer.get_pending_messages())} mensagens pendentes no buffer")
            
        self.iot_manager.disconnect()
        logger.info("👋 Aplicação encerrada")

# =========================
# Versão de Teste
# =========================
def test_conversion():
    """Testa a conversão de dados"""
    logger.info("🧪 Testando conversão de dados...")
    
    # Dados de exemplo do PlugField
    sample_data = {
        "data": "2026-01-15",
        "precipitacao_mm": 0.1,
        "temp_max": 31.4,
        "temp_min": 16.5,
        "umidade_media": 77.1
    }
    
    processor = DataProcessor()
    result = processor.convert_plugfield_to_simepar(sample_data)
    
    if result:
        logger.info("✅ Conversão bem-sucedida!")
        logger.info(f"Resultado: {json.dumps(result, indent=2)}")
        return True
    else:
        logger.error("❌ Conversão falhou!")
        return False

def test_connection():
    """Testa a conexão com AWS IoT"""
    logger.info("🔗 Testando conexão AWS IoT...")
    
    iot_manager = AWSIoTManager(AWS_CONFIG)
    
    # Testa conexão
    if iot_manager.connect():
        logger.info("✅ Conexão bem-sucedida!")
        
        # Testa publicação
        test_msg = {
            "test": True,
            "timestamp": datetime.now(TZ_SP).isoformat(),
            "message": "Teste de conexão"
        }
        
        if iot_manager.publish("test/connection", test_msg):
            logger.info("✅ Publicação bem-sucedida!")
        else:
            logger.error("❌ Falha na publicação")
            
        iot_manager.disconnect()
        return True
    else:
        logger.error("❌ Falha na conexão")
        return False

# =========================
# Função Principal
# =========================
def main():
    """Função principal"""
    import sys
    
    # Verifica argumentos
    if len(sys.argv) > 1:
        if sys.argv[1] == "--test":
            # Modo teste completo
            logger.info("🧪 Executando em modo de teste...")
            
            print("\n1. Testando conversão de dados...")
            if not test_conversion():
                sys.exit(1)
                
            print("\n2. Testando conexão AWS IoT...")
            if not test_connection():
                sys.exit(1)
                
            logger.info("✅ Todos os testes passaram!")
            sys.exit(0)
            
        elif sys.argv[1] == "--test-conversion":
            test_conversion()
            sys.exit(0)
            
        elif sys.argv[1] == "--test-connection":
            test_connection()
            sys.exit(0)
            
        elif sys.argv[1] == "--help":
            print("Uso:")
            print("  python3 plugfield_processor.py          # Modo normal")
            print("  python3 plugfield_processor.py --test   # Teste completo")
            print("  python3 plugfield_processor.py --test-conversion  # Teste conversão")
            print("  python3 plugfield_processor.py --test-connection  # Teste conexão")
            print("  python3 plugfield_processor.py --help   # Esta ajuda")
            sys.exit(0)
    
    # Modo normal
    logger.info("🚀 Iniciando PlugField Processor...")
    logger.info("Este serviço irá:")
    logger.info("  ✅ Escutar o tópico 'plugfield/forecast/daily'")
    logger.info("  ✅ Converter os dados para formato Simepar")
    logger.info("  ✅ Publicar no tópico 'previsao/simepar'")
    logger.info("  ✅ Salvar no S3 automaticamente")
    logger.info("  ✅ Buffer de mensagens quando offline")
    logger.info("  ✅ Reconexão automática")
    logger.info("  ✅ Monitoramento de saúde da conexão")
    
    app = PlugFieldProcessor()
    app.run()

if __name__ == "__main__":
    main()
