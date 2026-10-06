#include <Wire.h>
#include <HardwareSerial.h>
#include "DFRobotDFPlayerMini.h"
#include <ESP32Servo.h>          

#define I2C_ADDR 0x08 // Trùng khớp với địa chỉ trong file Python

// --- Cấu Hình Cổng & Chân ---
HardwareSerial dfSerial(1);     
DFRobotDFPlayerMini myDFPlayer; 
Servo servoX;                    

const int rxLoaPin = 16;         
const int txLoaPin = 17;         
const int pinServoX = 18;        
const int ledPin = 2;           

// --- Biến Toàn Cục Xử Lý Ngắt I2C ---
volatile boolean commandReceived = false;
String i2cCommand = "";

void setup() {
  Serial.begin(115200);          
  pinMode(ledPin, OUTPUT);       
  digitalWrite(ledPin, LOW);     

  Serial.println("=== KHOI DONG HE THONG ESP32 TONG HOP ===");

  // =========================================================
  // [BƯỚC QUAN TRỌNG NHẤT]: KHỞI TẠO I2C NGAY LẬP TỨC
  // Ép kiểu (uint8_t) để mạch hiểu là Slave, chỉ định thẳng chân 21 (SDA), 22 (SCL)
  // =========================================================
  bool i2cStatus = Wire.begin((uint8_t)I2C_ADDR, 21, 22, 100000); 
  Wire.onReceive(receiveEvent); 
  
  if(i2cStatus) {
    Serial.println("-> I2C Slave OK! ESP32 dang lang nghe Pi o dia chi 0x08");
  } else {
    Serial.println("-> LOI: Khong the khoi tao I2C!");
  }

  // =========================================================
  // 1. KHỞI TẠO VÀ TEST SERVO (Sau khi I2C đã sẵn sàng)
  // =========================================================
  ESP32PWM::allocateTimer(0);          
  servoX.setPeriodHertz(50);           
  servoX.attach(pinServoX, 500, 2400); 

  Serial.println("--> Bat dau kiem tra hanh trinh Servo..."); 
  servoX.write(20); delay(1000);
  servoX.write(160); delay(1000);
  servoX.write(90); delay(800);

  // =========================================================
  // 2. KHỞI TẠO VÀ TEST LOA DFPLAYER
  // =========================================================
  dfSerial.begin(9600, SERIAL_8N1, rxLoaPin, txLoaPin); 
  Serial.println("Dang khoi tao DFPlayer Mini...");     

  if (!myDFPlayer.begin(dfSerial)) {
    Serial.println("Loi: Khong tim thay DFPlayer!"); 
  } else {
    myDFPlayer.volume(25); 
    
    Serial.println("--> Kiem tra the nho...");
    int totalFiles = -1; 
    int retryCount = 0;  
    
    while (retryCount < 5) { 
      totalFiles = myDFPlayer.readFileCounts(DFPLAYER_DEVICE_SD); 
      if (totalFiles != -1 && totalFiles > 0) break; 
      retryCount++; 
      digitalWrite(ledPin, HIGH); delay(100); digitalWrite(ledPin, LOW); delay(900);
    }

    if (totalFiles > 0) {
      Serial.print("The nho OK! Tong so file: "); Serial.println(totalFiles);                 
      for (int i = 1; i <= 3; i++) { 
        myDFPlayer.playFolder(1, i); 
        delay(2500); 
      }
    }
  }

  digitalWrite(ledPin, HIGH); 
  Serial.println("Kiem tra hoan tat! Vao nhiem vu chinh."); 
}

void loop() {
  // Chỉ chạy khối lệnh này khi có sự kiện I2C báo hiệu
  if (commandReceived) {
    String cmd = i2cCommand;
    cmd.trim();

    Serial.print("[DEBUG I2C] ESP32 vua nhan duoc: '");
    Serial.print(cmd);
    Serial.println("'");

    // Xử lý lệnh của LOA
    if (cmd.startsWith("ALARM:")) {
      int code = cmd.substring(6).toInt();
      Serial.print("   -> Hop le! Phat am thanh thu muc 01, file: 00");
      Serial.print(code);
      Serial.println(".mp3");
      myDFPlayer.playFolder(1, code);
    } 
    // Xử lý lệnh của SERVO
    else if (cmd.startsWith("MOVE:")) {
      float offsetAngle = cmd.substring(5).toFloat();
      float targetAngle = 90 + offsetAngle;          
      targetAngle = constrain(targetAngle, 20, 160);  
      
      servoX.write(targetAngle); 
      Serial.print("   -> Hop le! Dang quay Servo toi goc: "); 
      Serial.println(targetAngle); 
    } 
    // Nếu lệnh rác
    else if (cmd.length() > 0) {
      Serial.println("   -> Lenh bi bo qua (Khong phai ALARM hay MOVE)!"); 
    }

    // Reset cờ để chờ lệnh mới
    i2cCommand = "";
    commandReceived = false;
  }
}

// Hàm ngắt (Interrupt) tự động được gọi bởi phần cứng khi có tín hiệu I2C đến
void receiveEvent(int howMany) {
  String tempCmd = "";
  
  while (Wire.available()) {
    char c = Wire.read();
    // Bỏ qua byte số 0 đầu tiên do thư viện smbus2 của Python thường tự động gán vào
    if (c != 0) {
      tempCmd += c;
    }
  }
  
  if (tempCmd.length() > 0) {
    i2cCommand = tempCmd;
    commandReceived = true; // Bật cờ cho hàm loop() xử lý
  }
}