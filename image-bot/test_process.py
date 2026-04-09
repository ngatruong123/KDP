"""Test nhanh phần xử lý ảnh. Dùng:
    python test_process.py ảnh.png
    python test_process.py ảnh.png output.png
Không cần Google API, không cần trình duyệt.
"""
import sys
import os

if len(sys.argv) < 2:
    print("Cách dùng: python test_process.py <ảnh_input> [ảnh_output]")
    sys.exit(1)

input_path = sys.argv[1]
if not os.path.exists(input_path):
    print(f"❌ Không tìm thấy: {input_path}")
    sys.exit(1)

output_path = sys.argv[2] if len(sys.argv) > 2 else input_path.rsplit('.', 1)[0] + '_VIP.png'

print(f"📥 Input:  {input_path}")
print(f"📤 Output: {output_path}")

from kdp_local_bot import process_single_image
result = process_single_image(input_path, output_path)

if result:
    print(f"\n✅ Thành công! Mở file xem: {output_path}")
else:
    print(f"\n❌ Thất bại!")
