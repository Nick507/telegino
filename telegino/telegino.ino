#include <EEPROM.h>
#include <OneWire.h>
#include <DallasTemperature.h>

#define LED_PORT 13

#define CMD_PING                   0
#define CMD_READ_OUT_PORT          1
#define CMD_OUT_PORT_LOW           2
#define CMD_OUT_PORT_HIGH          3
#define CMD_READ_INPUT_PORT        4
#define CMD_READ_INPUT_PULLUP_PORT 5
#define CMD_READ_DS18B20           6
#define CMD_READ_EEPROM            7

int digitalReadOutputPin(uint8_t pin)
{
  uint8_t bit = digitalPinToBitMask(pin);
  uint8_t port = digitalPinToPort(pin);
  if (port == NOT_A_PIN) return LOW;
  return (*portOutputRegister(port) & bit) ? HIGH : LOW;
}

uint8_t cfg[16];

void setup() 
{
  Serial.begin(115200);
  
  pinMode(LED_PORT, OUTPUT);
  digitalWrite(LED_PORT, LOW);

  for(int i = 2; i < 16; i++)
  {
    uint8_t b = EEPROM.read(i);
    cfg[i] = b;
    switch(b)
    {
      case CMD_OUT_PORT_HIGH:
        pinMode(i, OUTPUT);
        digitalWrite(i, HIGH);
        break;
      case CMD_OUT_PORT_LOW:
        pinMode(i, OUTPUT);
        digitalWrite(i, LOW);
        break;
      case CMD_READ_INPUT_PORT:
        pinMode(i, INPUT);
        break;
      case CMD_READ_INPUT_PULLUP_PORT:
        pinMode(i, INPUT_PULLUP);
        break;
    }
  }
}

void loop() 
{
  digitalWrite(LED_PORT, millis() & 0x200 ? HIGH : LOW);
  
  if(Serial.available())
  {
    uint8_t c = Serial.read();
    uint8_t port = c & 0x0F;
    uint8_t cmd = c >> 4;


    if(cmd > CMD_READ_OUT_PORT && cmd <= CMD_READ_DS18B20)
    {
      // protect RX/TX pins
      if(port < 2)
      {
        Serial.write(0xFF);
        return;
      }

      if(cfg[port] != cmd)
      {
        EEPROM.write(port, cmd);
        cfg[port] = cmd; // EEPROM.read(port) ?
      }
    }

    switch(cmd)
    {
      case CMD_PING:
        Serial.write(0xAA);
        break;
      case CMD_READ_OUT_PORT:
        Serial.write(digitalReadOutputPin(port));
        break;
      case CMD_OUT_PORT_LOW:
        pinMode(port, OUTPUT);
        digitalWrite(port, LOW);
        Serial.write(0xAA);
        break;
      case CMD_OUT_PORT_HIGH:
        pinMode(port, OUTPUT);
        digitalWrite(port, HIGH);
        Serial.write(0xAA);
        break;
      case CMD_READ_INPUT_PORT:
        pinMode(port, INPUT);
        Serial.write(digitalRead(port));
        break;
      case CMD_READ_INPUT_PULLUP_PORT:
        pinMode(port, INPUT_PULLUP);
        Serial.write(digitalRead(port));
        break;
      case CMD_READ_DS18B20:
        {
          OneWire oneWire(port); // 14 bytes
          DallasTemperature dt(&oneWire); // 23 bytes
          dt.begin();
          dt.requestTemperatures();
          Serial.println(dt.getTempCByIndex(0), 1);
        }
        break;
      case CMD_READ_EEPROM:
        for(int i = 0; i < 16; i++)
        {
          uint8_t b = EEPROM.read(i);
          uint8_t n = b >> 4;
          Serial.write(n > 9 ? n + 'A' - 10 : n + '0');
          n = b & 0x0F;
          Serial.write(n > 9 ? n + 'A' - 10 : n + '0');
        }
        break;
      default:
        Serial.write(0xFF);
    }
  }
}
