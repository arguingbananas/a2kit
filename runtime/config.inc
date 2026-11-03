; config.inc - build settings shared by receiver.s and video.s.
; Change these, then run "./a2kit build" and retype the new receiver.bin.

SLOT      = 2           ; Super Serial Card slot (1-7)

; 6551 control register: 1 stop bit, 8 data bits, internal baud clock, and the
; baud rate in the low 4 bits: $1F = 19200, $1E = 9600, $1C = 4800, $18 = 1200.
; If you change it, pass the matching --baud to send, show, stream and video.
ACIA_CTRL_VALUE = $1F

SLOTX     = SLOT * 16
