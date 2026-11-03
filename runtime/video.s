; video.s - a2kit streaming video player (HGR page 1, Super Serial Card)
;
; Reads a delta stream from the serial port and applies it straight to the
; screen. There is no flow control: bytes arrive at line rate (one per ~532
; cycles at 19200 baud, one per ~88.6 at 115200) and every path from one byte
; to the next finishes inside that (tools/test_video.py checks both speeds).
;
; Stream grammar. The key property: every DATA byte (literal bytes, long-skip
; arguments, audio page counts) is < $80, and every byte >= $80 is a control
; byte. HGR data is monochrome (bit 7 clear), so this costs nothing, and it
; means a control byte showing up where data should be proves the stream has
; lost sync.
;
;   $FE        frame start: next op writes from $2000 again (also the resync mark)
;   $01-$7E    literal run: n data bytes follow, written at the current address
;   $7F n c    audio burst: play n pages (n*64 ms) from RAM, then carry on;
;              c = n xor $55 (a stray $7F in damaged data rarely passes this)
;   $81-$FD    skip (b & $7F) bytes, 1-125
;   $FF lo hi  long skip of lo + 128*hi bytes (lo, hi < $80)
;   $00        ignored
;   $80 $80    end of stream: restore text mode and return (twice, so one
;              corrupted byte can't end the show)
; Any key also quits.
;
; Recovery: a control byte inside a run, a run or skip that would go past the
; end of the screen ($3FFF), or a bad audio burst makes the player discard
; bytes until the next $FE. So a lost or corrupted byte costs at most the rest
; of that one frame, the player never writes outside $2000-$3FFF, and audio is
; only ever read from $6000-$BFFF. Interrupts are off and decimal mode is
; cleared while it runs (both restored on return). Sound needs the normal
; 1.02 MHz clock: on an accelerated machine it plays at the wrong pitch.
;
; Audio: the speaker player is cycle-counted and can't listen to the serial
; port, so it only runs in bursts the host schedules between frames. The host
; waits out each burst before sending more (a byte arriving mid-burst would
; be lost). Audio data must already be in RAM at $6000 (uploaded earlier with
; the packet receiver); each burst continues where the last one stopped.
;
; Loads at $0E00 (player routine at $1000). Build: ca65 -I runtime runtime/video.s -o video.o
;                        ld65 -C runtime/video.cfg video.o -o video.bin

        .include "config.inc"           ; SLOT
AUDIO_BASE = $60                ; audio data starts at $6000

ACIA_DATA = $C088
ACIA_STAT = $C089

KBD     = $C000
KBDSTRB = $C010
SPKR    = $C030
TXTCLR  = $C050
TXTSET  = $C051
MIXCLR  = $C052
LOWSCR  = $C054
HIRES   = $C057

ptr     = $06                   ; 2 bytes: current screen address
cnt     = $08
tmp     = $09
tmp2    = $0A
aptr    = $0B                   ; 2 bytes: audio read pointer
apages  = $0D
atmp    = $0E
atmp2   = $0F

        .export lost, ctl, vframe
        .segment "CODE"

start:
        php                     ; keep the caller's interrupt state
        sei                     ; audio bursts are cycle-timed
        cld                     ; binary arithmetic (plp restores the caller's mode)
        lda #0                  ; clear HGR page 1 so the encoder knows the
        sta ptr                 ; starting state is black
        lda #$20
        sta ptr+1
clr:    lda #0
        tay
@c:     sta (ptr),y
        iny
        bne @c
        inc ptr+1
        lda ptr+1
        cmp #$40
        bne clr

        bit TXTCLR              ; show it
        bit MIXCLR
        bit LOWSCR
        bit HIRES

        ldx #SLOTX              ; the serial card is left exactly as the receiver
        bit KBDSTRB             ; set it up, so one player works at any speed
        lda #0
        sta aptr
        lda #AUDIO_BASE
        sta aptr+1

vframe: lda #0
        sta ptr
        lda #$20
        sta ptr+1
vop:    jsr getb
        beq vop                 ; $00: ignored
        bmi ctlop
        cmp #$7F
        bne @run
        jmp burst
@run:   sta cnt                 ; literal run of cnt bytes
        clc                     ; refuse a run that would end past $3FFF
        adc ptr
        sta tmp                 ; tmp = low byte of the run's end
        lda ptr+1
        adc #0
        cmp #$40
        bcc @fits               ; ends below $4000
        bne @nofit              ; ends at $41xx or later
        lda tmp
        beq @fits               ; ends exactly at $4000
@nofit: jmp lost
@fits:  ldy #0
@lit:   jsr getb
        bpl @data
        jmp ctl                 ; control byte where data belongs: lost sync
@data:  sta (ptr),y
        iny
        cpy cnt
        bne @lit
        tya
        clc
        adc ptr
        sta ptr
        bcc chk
        inc ptr+1
chk:    lda ptr+1               ; never let a bad stream walk off the screen
        cmp #$40
        bcc @ok                 ; below $4000
        bne @bad                ; $4100 or more
        lda ptr
        beq @ok                 ; exactly $4000: just past the end, which is fine
@bad:   jmp lost                ; (any further write or skip is refused)
@ok:    jmp vop

ctlop:  cmp #$FF
        beq @long
        cmp #$FE
        beq vframe
        cmp #$80
        beq endop
        and #$7F                ; skip 1-125
        clc
        adc ptr
        sta ptr
        bcc chk
        inc ptr+1
        jmp chk
@long:  jsr getb
        bmi ctl
        sta tmp                 ; lo (7 bits)
        jsr getb
        bmi ctl
        lsr a                   ; hi (7 bits): 128*hi = (hi>>1) high byte + (hi&1) << 7
        sta tmp2
        lda tmp
        bcc @n
        ora #$80
@n:     clc
        adc ptr
        sta ptr
        lda ptr+1
        adc tmp2
        sta ptr+1
        jmp chk

burst:  jsr getb                ; page count
        bmi ctl
        sta apages
        jsr getb                ; check byte: count xor $55
        bmi ctl
        eor #$55
        cmp apages
        bne lost                ; not a real burst
        lda apages
        bne @go
        jmp vop                 ; 0 pages: nothing to do
@go:    clc
        adc aptr+1
        bcs lost                ; wrapped past $FF00: certainly bad
        cmp #$C1                ; must stay within $6000-$BFFF
        bcs lost
        jsr play
        ldx #SLOTX              ; play uses X
        jmp vop

; a control byte turned up where data was expected (A = that byte)
ctl:    cmp #$FE
        beq resync
        cmp #$80
        beq endop
lost:   jsr getb                ; discard until the next frame mark / end
        cmp #$FE
        beq resync
        cmp #$80
        bne lost
endop:  jsr getb                ; a real end of stream is $80 $80
        cmp #$80
        beq vdone
        cmp #$FE
        beq resync
        bne lost                ; always
vdone:  bit TXTSET
        plp
        rts
resync: jmp vframe              ; (vframe is out of branch range from here)

; wait for a serial byte; a keypress aborts the whole player
getb:   lda KBD
        bmi @abort
        lda ACIA_STAT,x
        and #$08
        beq getb
        lda ACIA_DATA,x
        rts
@abort: pla                     ; drop getb's return address
        pla
        bit KBDSTRB
        bit TXTSET
        plp                     ; the php from start
        rts

        .include "speaker.inc"
