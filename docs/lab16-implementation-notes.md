# Hướng dẫn và Ghi chú Triển khai Lab 16 — Reflexion Agent

Tài liệu này ghi lại kiến thức thực tế, giải pháp thiết kế kiến trúc, và các bài học kinh nghiệm rút ra trong quá trình triển khai **Reflexion Agent** trên tập dữ liệu benchmark 100 câu hỏi HotpotQA sử dụng cả dịch vụ đám mây (Cloud DeepSeek) và mô hình cục bộ (Local Ollama).

---

## 1. Kiến trúc Hệ thống & Luồng Dữ liệu (Workflow)

Hệ thống được thiết kế theo mô hình tự phản chiếu (Self-Reflection Loop) khép kín với các thành phần chính được mô đun hóa:

```mermaid
graph TD
    Start[Bắt đầu: QAExample] --> Actor[1. Actor: Trả lời câu hỏi]
    Actor --> Eval[2. Evaluator: Chấm điểm kết quả]
    Eval -- Score = 1 --> Pass[Thành công: Lưu Trace & Kết thúc]
    Eval -- Score = 0 --> AttemptCheck{Đạt tối đa Attempts?}
    AttemptCheck -- Đúng --> Fail[Thất bại: Lưu Trace & Kết thúc]
    AttemptCheck -- Sai --> Reflect[3. Reflector: Tạo Reflection]
    Reflect --> Mem[Cập nhật reflection_memory]
    Mem --> Actor
```

### Chi tiết các bước:
1. **Actor (Tác tử hành động)**:
   - Nhận câu hỏi, ngữ cảnh hỗ trợ và bộ nhớ phản chiếu quá khứ (`reflection_memory`).
   - Sử dụng prompt hệ thống thiết kế riêng để tận dụng các chỉ dẫn từ các lần lỗi trước nhằm điều chỉnh câu trả lời mới.
2. **Evaluator (Tác tử đánh giá)**:
   - Thực hiện kiểm tra câu trả lời dự đoán đối chiếu với đáp án chuẩn (`gold_answer`).
   - Yêu cầu xuất ra định dạng JSON cấu trúc (để tránh lỗi phân tách) chứa điểm số (0 hoặc 1), lý do chấm điểm, các bằng chứng bị thiếu, và các thông tin sai lệch (hallucination).
3. **Reflector (Tác tử phản chiếu)**:
   - Chỉ được kích hoạt khi câu trả lời của Actor bị Evaluator chấm 0 điểm và chưa đạt giới hạn số lần thử.
   - Nhận diện lỗi sai từ phản hồi của Evaluator, viết phân tích nguyên nhân lỗi và đề xuất chiến thuật cụ thể cho lần thử tiếp theo.

---

## 2. Thiết lập Đa Nhà cung cấp (Multi-Provider Runtime)

Hệ thống hỗ trợ 3 chế độ chạy thông qua biến môi trường `LLM_PROVIDER`:

### Chế độ 1: Cloud Provider (DeepSeek-v4-flash)
*   **API Endpoint**: `https://opencode.ai/zen/go/v1/chat/completions`
*   **Model**: `deepseek-v4-flash`
*   **Cơ chế bypass WAF**: Khi sử dụng thư viện Python `urllib` mặc định, các yêu cầu HTTP thường bị Cloudflare chặn bằng lỗi `403 Forbidden` (mã lỗi 1010). Giải pháp là chèn thêm tiêu đề `User-Agent` của trình duyệt chuẩn vào cấu hình request.

### Chế độ 2: Local Provider (Ollama llama3.2:1b)
*   **API Endpoint**: `http://localhost:11434/v1/chat/completions` (sử dụng API tương thích OpenAI tích hợp sẵn của Ollama).
*   **Model**: `llama3.2:1b`
*   **Đặc điểm**: Do mô hình cục bộ có tham số nhỏ (1.2B), việc ép xuất JSON qua prompt đôi khi không ổn định. Hệ thống sử dụng thêm cơ chế `"response_format": {"type": "json_object"}` trong payload request và một bộ lọc Regex dự phòng (`parse_json_from_text`) để trích xuất JSON trong khối văn bản tự do.

### Chế độ 3: Mock Provider
*   **Đặc điểm**: Không tốn chi phí gọi API, chạy xác định (deterministic) dựa trên mã câu hỏi (`qid`). Được dùng để phát triển luồng cơ sở và kiểm tra độ ổn định của hệ thống autograde.

---

## 3. Hệ thống Thu thập Telemetry Thực tế (Thread-Safe Telemetry)

Để chấm điểm đúng yêu cầu đo lường năng lượng tiêu thụ (Token) và thời gian trễ (Latency) thay vì sử dụng mock số liệu tĩnh, chúng tôi đã triển khai hệ thống **Thread-Local Telemetry**:
*   Sử dụng thư viện `threading.local` để lưu trữ bộ đếm độc lập cho từng luồng khi chạy song song.
*   Trước mỗi vòng thử của một câu hỏi, bộ đếm được khởi tạo lại bằng hàm `reset_telemetry()`.
*   Mỗi lượt gọi LLM bên trong Actor, Evaluator, Reflector sẽ cộng dồn số token sử dụng thực tế (lấy từ trường `usage` của response) và thời gian chạy (tính bằng mili-giây) vào biến cục bộ của luồng đó.
*   Khi kết thúc một attempt, agent sẽ lấy tổng số liệu tích lũy qua `get_telemetry()` và ghi vào vết thực thi (`AttemptTrace`).

---

## 4. Phân loại Lỗi Động (Dynamic Failure Mode Classification)

Thay vì cố định mã lỗi dựa trên dữ liệu giả lập, hệ thống tự động phân loại lỗi sai của tác tử dựa trên vết thực thi và phản hồi của Evaluator:
1.  **`none`**: Câu trả lời hoàn toàn chính xác (Score = 1).
2.  **`looping`**: Tác tử bị lặp câu trả lời (câu trả lời ở lần thử cuối trùng khớp hoàn toàn với lần thử đầu tiên mặc dù đã có phản chiếu).
3.  **`incomplete_multi_hop`**: Trình đánh giá phát hiện câu trả lời thiếu thông tin hoặc dừng sớm (kiểm tra từ khóa phản hồi của Evaluator liên quan đến "missing", "incomplete" hoặc "stop").
4.  **`entity_drift`**: Tác tử bị lệch thực thể đích ở hop suy luận tiếp theo (phản hồi chứa từ khóa "drift", "spurious" hoặc "wrong second-hop").
5.  **`wrong_final_answer`**: Các trường hợp sai lệch chung khác.

---

## 5. Các Tính năng Mở rộng đã Triển khai (Bonus Extensions)

Hệ thống đã tích hợp 5 tính năng nâng cao giúp tăng độ ổn định và tối ưu chi phí:
*   **`structured_evaluator`**: Enforce JSON schema đầu ra cho Evaluator.
*   **`reflection_memory`**: Lưu trữ lịch sử bài học và nối tiếp ngữ cảnh thông minh qua các lượt thực thi.
*   **`benchmark_report_json`**: Xuất báo cáo cấu trúc chi tiết để phục vụ chấm điểm tự động.
*   **`mock_mode_for_autograding`**: Cho phép chạy kiểm thử autograding tức thì không mất phí.
*   **`adaptive_max_attempts`**: Điều chỉnh động số lần thử tối đa dựa vào độ khó của câu hỏi:
    *   Câu hỏi `easy`: Tối đa 2 lần thử.
    *   Câu hỏi `medium`: Tối đa 3 lần thử.
    *   Câu hỏi `hard`: Tối đa 4 lần thử.

---

## 6. Bài học Thực tế & Khuyến nghị

1.  **Chạy song song (Parallel execution)**: Khi chạy 100 câu hỏi, việc chạy tuần tự sẽ rất chậm (khoảng 10-15 phút). Sử dụng `ThreadPoolExecutor` với 10 worker giúp hoàn thành toàn bộ benchmark trong khoảng 2.5 phút trên Cloud DeepSeek.
2.  **Ảo tưởng tự sửa lỗi**: LLM rất tin tưởng suy nghĩ ban đầu của nó. Phản hồi phê bình của Evaluator phải cực kỳ rõ ràng, chỉ rõ lỗi sai cụ thể thì Reflector mới đề xuất được chiến thuật sửa đổi tốt.
3.  **JSON Robustness**: Luôn phải có hàm lọc Regex dự phòng đối với dữ liệu trả về của LLM vì thỉnh thoảng các ký tự đặc biệt (ví dụ như dấu ngoặc kép chưa escape như trong cụm `I"s`) sẽ làm vỡ trình phân tích JSON mặc định.
