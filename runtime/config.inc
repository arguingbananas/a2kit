; config.inc - build settings for receiver.s (and boot.s, which includes the receiver).
; Change these, then run "./a2kit build" and boot the new disk (or retype receiver.bin).

SLOT      = 2           ; Super Serial Card slot (1-7)

; Line speed. The Makefile builds two receivers: the default here (19200) and
; one with -D BAUD=115200. Pass the matching --baud to the a2kit commands (or
; set A2KIT_BAUD once). The video player and the sequencer keep whatever speed
; the receiver set, so only the receiver and boot disk differ between speeds.
.ifndef BAUD
BAUD = 19200
.endif

; 6551 control register: 1 stop bit, 8 data bits, and the rate in the low 4 bits.
; $1F..$16 use the 6551's baud-rate generator. $10 ("16x external clock") turns
; it off and runs straight from the card's 1.8432 MHz crystal / 16 = 115200,
; the same setting ADTPro uses on an unmodified Super Serial Card.
.if BAUD = 115200
ACIA_CTRL_VALUE = $10
.elseif BAUD = 19200
ACIA_CTRL_VALUE = $1F
.elseif BAUD = 9600
ACIA_CTRL_VALUE = $1E
.elseif BAUD = 4800
ACIA_CTRL_VALUE = $1C
.elseif BAUD = 2400
ACIA_CTRL_VALUE = $1A
.elseif BAUD = 1200
ACIA_CTRL_VALUE = $18
.else
.error "BAUD must be 115200, 19200, 9600, 4800, 2400 or 1200"
.endif

SLOTX     = SLOT * 16
