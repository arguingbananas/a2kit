; screenhold.s - show hi-res page 1 until the computer says go on, for a2kit chargen.
; Loads at $1200. $1203 picks the screen: 0 = full hi-res, 1 = mixed (hi-res with
; the four text lines at the bottom). It waits for any byte on the serial line
; (which it takes, so the receiver never sees it) or a key, then returns to the
; receiver with carry clear, so a script can step through screens with no one at
; the keyboard. Interrupts stay as the receiver left them (off).
; Build: ca65 -I runtime runtime/screenhold.s -o screenhold.o
;        ld65 -C runtime/screenhold.cfg screenhold.o -o screenhold.bin

        .include "config.inc"           ; SLOT

KBD     = $C000
KBDSTRB = $C010
TXTCLR  = $C050
MIXCLR  = $C052
MIXSET  = $C053
LOWSCR  = $C054
HIRES   = $C057
DATA    = $C088 + SLOTX         ; the 6551, already set up by the receiver
STAT    = $C089 + SLOTX

        .segment "CODE"

        jmp start
mode:   .byte 0                 ; set by the computer

start:  bit TXTCLR
        bit HIRES
        bit LOWSCR
        lda mode
        beq @full
        bit MIXSET
        jmp wait
@full:  bit MIXCLR
wait:   lda KBD
        bmi key
        lda STAT
        and #$08                ; a byte has arrived
        beq wait
        lda DATA
        clc
        rts
key:    bit KBDSTRB
        clc
        rts
