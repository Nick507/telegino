import sys
import telebot
import logging
from telebot import types
import serial
import struct
import threading
from time import sleep
import json
import datetime
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.dates as md

logger = logging.getLogger()

logging.basicConfig(filename='telegino.log',
                    level=logging.INFO,
                    format='%(asctime)s.%(msecs)03d %(levelname)s %(filename)s:%(lineno)d %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')

logger.addHandler(logging.StreamHandler(sys.stdout))

config = {}

try:
    f = open('config.json', 'r', encoding='utf-8')
    config = json.load(f)
    #print(config)
except Exception as e:
    logger.error("Can't read config file config.json: {}".format(str(e)))
    exit()

bot = telebot.TeleBot(config['token'])
comPort = serial.Serial()
connectionState = 0
stateMessages = ['Неизвестное состояние', 'Контроллер подключен и отвечает', 'Контроллер подключен но не отвечает', 'Контроллер не подключен']
devices = []
workingFlag = True
connectionMutex = threading.Lock()
nextLogTime = datetime.datetime.now()
nextPollTime = datetime.datetime.now()

matplotlib.use('agg')

# ==================================================================================================================
#
#                                        Arduino API
#
# ==================================================================================================================

def connect():
    if(comPort.isOpen()): return True
    comPort.port = config['port']
    comPort.baudrate = 115200
    comPort.timeout = 1
    comPort.setDTR(False)
    try:
        comPort.open()
    except:
        return False
    return True

def setPort(port, state):
    cmd = 0x30 if state else 0x20
    cmd = cmd | port
    data = struct.pack("B", cmd)
    comPort.write(data)
    return (struct.unpack("B", comPort.read(1))[0] == 0xAA)

def getOutPortState(port):
    data = struct.pack("B", 0x10 | port)
    comPort.write(data)
    return (struct.unpack("B", comPort.read(1))[0])

def ping():
    try:
        comPort.write(struct.pack("B", 0))
        res = comPort.read(1)
        if(len(res) < 1): return False
        return (struct.unpack("B", res)[0] == 0xAA)
    except Exception as e:
        logger.error(str(e))
        comPort.close()
    return False

def requestTemperature(port):
    global connectionError
    try:
        comPort.write(struct.pack("B", 0x60 | port))
        return float(comPort.readline().decode().strip())
    except Exception as e:
        logger.error(str(e))

def setDeviceState(state):
    global connectionState, stateMessages
    if(state == connectionState): return
    connectionState = state
    logger.info(stateMessages[connectionState])
    sendBroadcastMessage(stateMessages[connectionState])

# ==================================================================================================================
#
#                                        Devices API
#
# ==================================================================================================================

class Device():
    def __init__(self, dev):
        self.name = dev['name']
        self.port = dev['port']
    def getState(self):
        pass
    def getCommands(self):
        return None
    def poll(self):
        pass
    def handleCommand(self, command):
        pass
    def getName(self):
        return self.name
    def getJsonLog(self):
        pass
    def hasChart(self):
        return False

# ---------------------------------------------------------------------------------------------------------------

class DOut(Device):
    def __init__(self, dev):
        Device.__init__(self, dev)
        self.state = 0
    def getState(self):
        return self.name + " : " + ("включен" if self.state else "выключен")
    def poll(self):
        self.state = getOutPortState(self.port)
    def getCommands(self):
        return [("Выключить" if self.state else "Включить") + " " + self.name]
    def handleCommand(self, command):
        if "Выключить" in command:
            self.state = 0
        elif "Включить" in command:
            self.state = 1
        setPort(self.port, self.state)
    def getJsonLog(self):
        return self.state
# ---------------------------------------------------------------------------------------------------------------

class DS18B20(Device):
    def __init__(self, dev):
        Device.__init__(self, dev)
        self.temp = -127.0
        self.adjust = dev['adjust'] if 'adjust' in dev else 0.0
        if 'alarms' in dev:
            self.alarms = dev['alarms']
        else: self.alarms = []
    def getStrVal(self):
        return '{:.1f}'.format(self.temp)
    def getState(self):
        res = self.name + " : " + self.getStrVal()
        if self.alarms:
            for alarm in self.alarms:
                if 'raised' not in alarm: continue
                res += ' (тревога {} {:.1f})'.format('>=' if alarm['on'] > alarm['off'] else '<=', alarm['on'])
        return res
    def getJsonLog(self):
        return self.temp
    def hasChart(self):
        return True
    def poll(self):
        temp = requestTemperature(self.port)
        if not temp: return
        self.temp = temp + self.adjust;

        if self.alarms:
            for alarm in self.alarms:
                # upper alarm
                if alarm['on'] > alarm['off']:
                    if self.temp >= alarm['on'] and 'raised' not in alarm:
                        alarm['raised'] = True
                        sendBroadcastMessage('{} тревога {:.1f} >= {:.1f}'.format(self.name, self.temp, alarm['on']))
                    if self.temp <= alarm['off'] and 'raised' in alarm:
                        del alarm['raised']
                        sendBroadcastMessage('{} значение в норме {:.1f} <= {:.1f}'.format(self.name, self.temp, alarm['off']))
                # lower alarm
                elif alarm['on'] < alarm['off']:
                    if self.temp <= alarm['on'] and 'raised' not in alarm:
                        alarm['raised'] = True
                        sendBroadcastMessage('{} тревога {:.1f} <= {:.1f}'.format(self.name, self.temp, alarm['on']))
                    if self.temp >= alarm['off'] and 'raised' in alarm:
                        del alarm['raised']
                        sendBroadcastMessage('{} значение в норме {:.1f} >= {:.1f}'.format(self.name, self.temp, alarm['off']))
# ---------------------------------------------------------------------------------------------------------------

def loadDevices():
    for dev in config['devices']:
        if dev['type'] == 'dout': devices.append(DOut(dev))
        elif dev['type'] == 'ds18b20': devices.append(DS18B20(dev))
        else:
            logger.error("Unknown device type: ", dev['type'])
            return False
    return True

# ---------------------------------------------------------------------------------------------------------------

def poll():
    global workingFlag, nextLogTime, nextPollTime

    while workingFlag:
        # poll
        td = datetime.timedelta(seconds=config['pollPeriod'])
        if datetime.datetime.now() - nextPollTime >= td:
            nextPollTime += td
            connectionMutex.acquire()
            if(connect()):
                if(ping()):
                    setDeviceState(1)
                    for dev in devices:
                        dev.poll()
                # print(, " temp=", currentTemp1)
                else:
                    setDeviceState(2)
            else:
                setDeviceState(3)
            connectionMutex.release()

        # write log
        td = datetime.timedelta(seconds=config['logPeriod'])
        if datetime.datetime.now() - nextLogTime >= td:
            nextLogTime += td
            connectionMutex.acquire()
            log = {}
            log['time'] = datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S")
            for dev in devices:
                log[dev.getName()] = dev.getJsonLog()
            with open('devices.log', 'a+', encoding='utf-8') as f:
                json.dump(log, f, ensure_ascii=False)
                f.write('\n')
            connectionMutex.release()

        sleep(0.5)

# ==================================================================================================================
#
#                                        Telebot
#
# ==================================================================================================================

def getMarkup():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True) #row_width=4
    markup.add(types.KeyboardButton('Статус'))
    markup.add(types.KeyboardButton('График за сутки'))
    markup.add(types.KeyboardButton('График за неделю'))
    markup.add(types.KeyboardButton('График за месяц'))
    for dev in devices:
        commands = dev.getCommands()
        if commands:
            for cmd in commands:
                markup.add(types.KeyboardButton(cmd))
    return markup

def sendBroadcastMessage(msg, exceptThis = None):
    global config
    try:
        for chatId in config['chatsWhiteList']:
            if(not exceptThis or chatId != exceptThis):
                bot.send_message(chatId, msg, reply_markup=getMarkup())
    except Exception as e:
        logger.error(str(e))

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    global config
    if (message.chat.id not in config['chatsWhiteList']):
        bot.send_message(message.chat.id, 'Отказано в доступе')
        logger.error("Access denided chatId: {} userName: {} message: {}".format(message.chat.id, message.from_user.full_name, message.text))
        return
    bot.send_message(message.chat.id, "PelatroNNLab бот приветствует тебя. Выбери команду", reply_markup = getMarkup())

def sendChart(message, interval):
    global devices

    fig = plt.figure(tight_layout=True)
    ax = plt.axes()
    now = datetime.datetime.now()
    xs = []
    charts = {}

    for dev in devices:
        if dev.hasChart():
            charts[dev.getName()] = []

    with open('devices.log', 'r', encoding='utf-8') as f:
        while True:
            line = f.readline()
            if not line: break
            j = json.loads(line)
            ts = datetime.datetime.strptime(j['time'], '%Y/%m/%d %H:%M:%S')
            if now - ts <= datetime.timedelta(hours=interval):
                # print(j)
                xs.append(ts)
                for devName, devData in charts.items():
                    devData.append(j[devName])

    for i, (devName, devData) in enumerate(charts.items()):
        ax.plot(xs, devData, color="rgbcmk"[i], label=devName)

    if interval <= 24:
        ax.xaxis.set_major_formatter(md.DateFormatter('%H:%M'))
    elif interval <= 24*7:
        ax.xaxis.set_major_formatter(md.DateFormatter('%a %H:%M'))
    else:
        ax.xaxis.set_major_formatter(md.DateFormatter('%d %b'))

    plt.minorticks_on()
    # plt.grid(visible=True, which='both')
    plt.grid(visible=True, which='major', linestyle='-')
    plt.grid(visible=True, which='minor', linestyle='dotted')
    plt.xticks(rotation=90, ha='center')
    plt.subplots_adjust(left=0.1, right=0.95, top=0.95, bottom=0.20)
    # plt.title('Температура')
    # plt.ylabel('Temperature (deg C)')
    plt.legend()
    plt.savefig('chart.png')
    bot.send_photo(message.chat.id, open('chart.png', 'rb'))

# labels = ','.join('\'{:02d}:00\''.format(t) for t in range(24))
#         values1 = ','.join('{:.1f}'.format(t) for t in [random.randrange(-20, 20) for _ in range(24)])
#         values2 = ','.join('{:.1f}'.format(t) for t in [random.randrange(-20, 20) for _ in range(24)])
#         chart = "https://quickchart.io/chart?c={type:'line',data:{labels:[" + labels + "],datasets:[{label:'Температура 1',fill:false,data:[" + values1 + "]},{label:'Температура 2',fill:false,data:[" + values2 + "]}]}}"

@bot.message_handler(func=lambda message: True)
def echo_all(message):
    global out1, out2, currentTemp1, config

    if(message.chat.id not in config['chatsWhiteList']):
        bot.send_message(message.chat.id, 'Отказано в доступе')
        logger.error("Access denided chatId: {} userName: {} message: {}".format(message.chat.id, message.from_user.full_name, message.text))
        return

    if (message.text == "Статус"):
        statusText = stateMessages[connectionState]
        if(connectionState == 1):
            for dev in devices:
                statusText += '\n' + dev.getState()
        bot.send_message(message.chat.id, statusText, reply_markup=getMarkup())
    elif(message.text == "График за сутки"):
        sendChart(message, 24)
    elif (message.text == "График за неделю"):
        sendChart(message, 24 * 7)
    elif (message.text == "График за месяц"):
        sendChart(message, 24 * 30)
    else:
        if (connectionState == 1):
            processed = False
            connectionMutex.acquire()
            for dev in devices:
                commands = dev.getCommands()
                if commands and message.text in commands:
                    dev.handleCommand(message.text)
                    sendBroadcastMessage(message.from_user.full_name + " " + message.text, exceptThis = message.chat.id)
                    bot.send_message(message.chat.id, dev.getState(), reply_markup=getMarkup())
                    processed = True
            connectionMutex.release()
            if not processed:
                bot.reply_to(message, "Неизвестная команда", reply_markup = getMarkup())
        else:
            bot.send_message(message.chat.id, stateMessages[connectionState], reply_markup=getMarkup())

# ==================================================================================================================
#
#                                        Main
#
# ==================================================================================================================

logger.info('Started')
if not loadDevices(): exit()
sendBroadcastMessage('Сервер стартован')
threading.Thread(target=poll).start()
bot.infinity_polling()
workingFlag = False