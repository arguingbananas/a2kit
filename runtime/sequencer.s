; sequencer.s - a2kit scene sequencer and speaker audio player
;
; Walks a scene table at $0B00 (6 bytes per scene, op 0 ends it):
;
;   op 1 SHOW  a=page (1 = $2000, 2 = $4000)      show that HGR page
;   op 2 PLAY  b=start page hi, c=number of pages  play 4-bit audio
;   op 3 WAIT  a=ticks lo, b=ticks hi              wait (1 tick = 0.1 s)
;   op 0 END
;
; Any key stops the show between scenes (audio can't be interrupted: it's
; cycle-timed). Memory map: table $0B00, this code $0C00-$0DFF, HGR pages
; $2000/$4000, audio data from $6000 (must be page aligned).
;
; Interrupts are off and decimal mode is cleared while it runs; both are
; restored afterwards. Runs at the normal 1.02 MHz only: the sound and the
; waits are timed by counting cycles.
;
; Build: ca65 -I runtime runtime/sequencer.s -o sequencer.o
;        ld65 -C runtime/sequencer.cfg sequencer.o -o sequencer.bin

SPKR    = $C030
KBD     = $C000
KBDSTRB = $C010
TXTCLR  = $C050
TXTSET  = $C051
MIXCLR  = $C052
LOWSCR  = $C054
HISCR   = $C055
HIRES   = $C057

table   = $0B00

OP_END  = 0
OP_SHOW = 1
OP_PLAY = 2
OP_WAIT = 3

aptr    = $06                   ; 2 bytes: audio read pointer (low byte = 0)
atmp    = $08
atmp2   = $09
apages  = $EB
idx     = $EC                   ; offset into scene table
tk      = $ED                   ; 2 bytes: wait ticks left

        .segment "CODE"

start:
        php                     ; keep the caller's interrupt state
        sei                     ; the audio player is cycle-timed
        cld                     ; binary arithmetic (plp restores the caller's mode)
        lda #0
        sta idx
next:   ldy idx
        lda table,y
        beq done
        cmp #OP_SHOW
        beq do_show
        cmp #OP_PLAY
        beq do_play
        cmp #OP_WAIT
        beq do_wait
done:   bit LOWSCR              ; page 1: PAGE2 also switches the TEXT screen to
        bit TXTSET              ; page 2 ($0800, our own code), so reset it first
        plp
        rts

do_show:
        bit TXTCLR
        bit MIXCLR
        bit HIRES
        lda table+1,y
        cmp #2
        beq @p2
        bit LOWSCR
        jmp adv
@p2:    bit HISCR
        jmp adv

do_play:
        lda #0
        sta aptr
        lda table+2,y
        sta aptr+1
        lda table+3,y
        sta apages
        jsr play
        jmp adv

do_wait:
        lda table+1,y
        sta tk
        lda table+2,y
        sta tk+1
@tick:  lda tk
        ora tk+1
        beq adv
        jsr tickdly
        bcs key
        lda tk
        bne @dec
        dec tk+1
@dec:   dec tk
        jmp @tick

adv:    lda idx
        clc
        adc #6
        sta idx
        lda KBD
        bmi key
        jmp next
key:    bit KBDSTRB
        jmp done

; ~0.1 s (79 * 1286 cycles); carry set if a key was pressed
tickdly:
        lda KBD
        bmi @key
        ldy #79
@o:     ldx #0
@i:     dex
        bne @i
        dey
        bne @o
        clc
        rts
@key:   sec
        rts

        .include "speaker.inc"
