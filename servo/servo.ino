//////////////////////////////////////////////////////////////////////////////////////
// Định nghĩa chân TX và RX cho UART2
// Trên ESP32: TX2 là chân 17, RX2 là chân 16
#define RX_PIN 16
#define TX_PIN 17

void setup() {
  // Serial mặc định để debug qua máy tính
  Serial.begin(115200);

  // Serial2 để giao tiếp với ESP2
  // Cấu hình tốc độ baud phải khớp với ESP2 (ví dụ 9600)
  Serial2.begin(9600, SERIAL_8N1, RX_PIN, TX_PIN);
  
  Serial.println("ESP1 da san sang gui du lieu!");
}

void loop() {
  // Ví dụ: Gửi một chuỗi dữ liệu mỗi 2 giây
  String dataToSend = "ALARM:1";
  
  // Gửi qua Serial2
  Serial2.println(dataToSend);
  
  // In ra Serial monitor để kiểm tra
  Serial.print("Da gui: ");
  Serial.println(dataToSend);
  
  delay(20000);
}