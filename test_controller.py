import vgamepad as vg
import time

# Create a virtual Xbox 360 controller
# (This is the most compatible with Remote Play)
gamepad = vg.VX360Gamepad()

print("Virtual Controller Spawned!")
print("Check your 'Game Controllers' window now.")
print("The script will close in 15 seconds...")

# Keep the script running so the controller stays active
time.sleep(15)

print("Virtual Controller Dismissed.")