import board, digitalio, time

led = digitalio.DigitalInOut(board.A2)
led.switch_to_output(False)

btn = digitalio.DigitalInOut(board.A3)
btn.switch_to_input(pull=digitalio.Pull.DOWN)

presses = 0
was_pressed = False
print("Ready: press the button")

while True:
    pressed = btn.value        # False = pressed for this button
    led.value = pressed              # LED on while held
    if pressed and not was_pressed:
        presses += 1
        print("PRESSED", presses)
    was_pressed = pressed
    time.sleep(0.02)