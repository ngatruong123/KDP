import sys
from kdp_local_bot import process_single_image

try:
    print("Testing process...")
    process_single_image('/Users/ngatruong/Work-Spacing/Code/remakeai/image-bot/kdp_workspace/0_HinhGoc/12_2.png', '/Users/ngatruong/Work-Spacing/Code/remakeai/image-bot/kdp_workspace/3_ThanhPham_VIP/12_2.png')
    print("Test finished.")
except Exception as e:
    print(f"Exception: {e}")
