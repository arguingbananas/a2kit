; video.s - a2kit streaming video player (HGR pages 1 and 2, Super Serial Card)
;
; Reads a delta stream from the serial port and applies it straight to the
; screen, or to the hidden page for page flipping. There is no flow control: bytes arrive at line rate (one per ~532
; cycles at 19200 baud, one per ~88.6 at 115200) and every path from one byte
; to the next finishes inside that (tools/test_video.py checks both speeds).
;
; Stream grammar. The key property: every DATA byte (literal bytes, long-skip
; arguments, audio page counts) is < $80, and every byte >= $80 is a control
; byte, so a control byte showing up where data should be proves the stream
; has lost sync. A screen byte's bit 7 is its palette bit (colour only): it
; isn't sent with the byte, but set for the bytes that follow by $FC / $FD.
;
;   $FE        frame start on page 1: next op writes from $2000 (also the resync mark)
;   $FB        frame start on page 2: next op writes from $4000 (also a resync mark)
;              Each frame start first shows the page the frame before was drawn
;              on, so a stream that alternates $FB and $FE draws every frame on
;              the hidden page and flips to it when it's complete; one that only
;              sends $FE draws on the page being shown, as before page 2 existed.
;   $01-$7E    literal run: n data bytes follow, written at the current address
;   $7F n c    audio burst: play n pages (n*64 ms) from RAM, then carry on;
;              c = n xor $55 (a stray $7F in damaged data rarely passes this)
;   $81-$FA    skip (b & $7F) bytes, 1-122
;   $FC        palette bit off for the literal bytes that follow (the default
;              at each frame start, so a black and white stream never sends it)
;   $FD        palette bit on for the literal bytes that follow
;   $FF lo hi  long skip of lo + 128*hi bytes (lo, hi < $80)
;   $00        ignored
;   $80 $80    end of stream: restore text mode and return (twice, so one
;              corrupted byte can't end the show)
; Any key also quits.
;
; Recovery: a control byte inside a run, a run or skip that would go past the
; end of the page being drawn ($3FFF or $5FFF), or a bad audio burst makes the
; player discard bytes until the next $FE or $FB. So a lost or corrupted byte
; costs at most the rest of that one frame, the player never writes outside
; the page the frame started on ($2000-$3FFF or $4000-$5FFF), and audio is
; only ever read from $6000-$BFFF or a language card's $D000-$FEFF. Interrupts are off and decimal mode is
; cleared while it runs (both restored on return). Sound needs the normal
; 1.02 MHz clock: on an accelerated machine it plays at the wrong pitch.
;
; Audio: the speaker player is cycle-counted and can't listen to the serial
; port, so it only runs in bursts the host schedules between frames. The host
; waits out each burst before sending more (a byte arriving mid-burst would
; be lost). Audio data must already be in RAM at $6000 (uploaded earlier with
; the packet receiver), carrying on at $D000 in a language card if it's long
; (see splay in speaker.inc); each burst continues where the last one stopped.
;
; Loads at $0E00 (player routine at $1000). Build: ca65 -I runtime runtime/video.s -o video.o
;                        ld65 -C runtime/video.cfg video.o -o video.bin

        .include "config.inc"           ; SLOT
AUDIO_BASE = $60                ; audio data starts at $6000
MARK    = $9B                   ; in the screen holes, for vsync (never a shown byte)

ACIA_DATA = $C088
ACIA_STAT = $C089

KBD     = $C000
KBDSTRB = $C010
SPKR    = $C030
TXTCLR  = $C050
TXTSET  = $C051
MIXCLR  = $C052
LOWSCR  = $C054
HISCR   = $C055
HIRES   = $C057

ptr     = $06                   ; 2 bytes: current screen address
cnt     = $08
tmp     = $09
tmp2    = $0A
aptr    = $0B                   ; 2 bytes: audio read pointer
apages  = $0D
atmp    = $0E
atmp2   = $0F
pal     = $19                   ; $80 or $00: the palette bit for literal bytes
top     = $1A                   ; $40 or $60: the end of the page being drawn
sw      = $1B                   ; <LOWSCR or <HISCR: the switch that shows that page

        .export lost, ctl, vframe, vframe2
        .segment "CODE"

start:
        php                     ; keep the caller's interrupt state
        sei                     ; audio bursts are cycle-timed
        cld                     ; binary arithmetic (plp restores the caller's mode)
        lda #0                  ; clear both HGR pages so the encoder knows
        sta ptr                 ; the starting state is black
        lda #$20
        sta ptr+1
clr:    lda #0
        tay
@c:     sta (ptr),y
        iny
        bne @c
        lda #MARK               ; the screen holes ($xx78-$xx7F, $xxF8-$xxFF)
        ldy #$78                ; get the marker vsync looks for
@h:     sta (ptr),y
        iny
        cpy #$80
        bne @h
        ldy #$F8
@h2:    sta (ptr),y
        iny
        bne @h2
        inc ptr+1
        lda ptr+1
        cmp #$60
        bne clr
        lda #$40                ; page 1 is drawn on (and shown) until a $FB
        sta top
        lda #<LOWSCR
        sta sw

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

vframe: ldy sw                  ; $FE: draw on page 1. First show the page
        cpy #<HISCR             ; the last frame was drawn on (LOWSCR or HISCR),
        bne @show               ; at the next vertical blank if that's a flip
        jsr vsync
        ldy sw
@show:  lda $C000,y
        lda #<LOWSCR
        sta sw
        lda #$40
        sta top
        lda #$20
frame:  sta ptr+1               ; start of the new frame's page
        lda #0
        sta ptr
        sta pal                 ; each frame starts with the palette bit off
vop:    jsr getb
        beq vop                 ; $00: ignored
        bmi ctlop
        cmp #$7F
        bne @run
        jmp burst
@run:   sta cnt                 ; literal run of cnt bytes
        clc                     ; refuse a run that would end past the page
        adc ptr
        sta tmp                 ; tmp = low byte of the run's end
        lda ptr+1
        adc #0
        cmp top
        bcc @fits               ; ends below $4000 (or $6000)
        bne @nofit              ; ends at $41xx (or $61xx) or later
        lda tmp
        beq @fits               ; ends exactly at the end of the page
@nofit: jmp lost
@fits:  ldy #0
@lit:   jsr getb
        bpl @data
        jmp ctl                 ; control byte where data belongs: lost sync
@data:  ora pal
        sta (ptr),y
        iny
        cpy cnt
        bne @lit
        tya
        clc
        adc ptr
        sta ptr
        bcc chk
        inc ptr+1
chk:    lda ptr+1               ; never let a bad stream walk off the page
        cmp top
        bcc @ok                 ; below $4000 (or $6000)
        bne @bad                ; $4100 (or $6100) or more
        lda ptr
        beq @ok                 ; exactly at the end: just past it, which is fine
@bad:   jmp lost                ; (any further write or skip is refused)
@ok:    jmp vop

ctlop:  cmp #$FF
        beq @long
        cmp #$FE
        beq vframe
        cmp #$FB
        beq vframe2
        cmp #$80
        bne @skip
        jmp endop               ; (out of branch range)
@skip:  cmp #$FC
        bcs @pal
        and #$7F                ; skip 1-122
        clc
        adc ptr
        sta ptr
        bcc chk
        inc ptr+1
        jmp chk
@pal:   lsr a                   ; $FC -> carry 0, $FD -> carry 1
        lda #0
        ror a                   ; ... -> $00 or $80
        sta pal
        jmp vop
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

vframe2:
        ldy sw                  ; $FB: draw on page 2 (as vframe, the other way)
        cpy #<LOWSCR
        bne @show
        jsr vsync
        ldy sw
@show:  lda $C000,y
        lda #<HISCR
        sta sw
        lda #$60
        sta top
        lda #$40
        jmp frame               ; (out of branch range)

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
        ldy aptr+1
        cpy #$C1
        bcs @play               ; already in the card: the wrap check was enough
        cmp #$F0                ; from main RAM: $C000 up to $EF00 means $D000 up to $FF00
        bcs lost
@play:  jsr splay
        ldx #SLOTX              ; play uses X
        jmp vop

; a control byte turned up where data was expected (A = that byte)
ctl:    cmp #$FE
        beq resync
        cmp #$FB
        beq resync2
        cmp #$80
        beq endop
lost:   jsr getb                ; discard until the next frame mark / end
        cmp #$FE
        beq resync
        cmp #$FB
        beq resync2
        cmp #$80
        bne lost
endop:  jsr getb                ; a real end of stream is $80 $80
        cmp #$80
        beq vdone
        cmp #$FE
        beq resync
        cmp #$FB
        beq resync2
        bne lost                ; always
vdone:  bit TXTSET
        bit LOWSCR              ; page 2 of text is the receiver's memory
        plp
        rts
resync: jmp vframe              ; (vframe is out of branch range from here)
resync2:
        jmp vframe2

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
        bit LOWSCR
        plp                     ; the php from start
        rts

; wait for the start of the vertical blank (II, II+ and IIe alike). Reading a
; soft switch returns the byte the video circuit last read (the floating bus).
; In hi-res it reads the screen holes, which hold MARK and are never shown,
; only during the top third of the screen and the vertical blank. So: wait
; until about 6 lines go by without MARK (the beam is in the lower two
; thirds), and the next MARK is the start of the blank. Takes up to one screen
; (17,030 cycles); the host stays quiet meanwhile. Gives up after 200 restarts
; or about 230 lines of waiting (no floating bus, an accelerator), so it never
; hangs. Uses A, Y and tmp; X (the slot) is kept.
vsync:  lda #200
        sta tmp
@quiet: ldy #20                 ; 20 x 21 cycles with no MARK, 2 reads each
@q:     lda HIRES
        cmp #MARK
        beq @again
        lda HIRES
        cmp #MARK
        beq @again
        dey
        bne @q
        lda #2                  ; then the first MARK (3 reads every 29 cycles,
        sta tmp                 ; which drifts against the 65-cycle line)
@w:     lda HIRES
        cmp #MARK
        beq @done
        lda HIRES
        cmp #MARK
        beq @done
        lda HIRES
        cmp #MARK
        beq @done
        dey
        bne @w
        dec tmp
        bne @w
@done:  rts
@again: dec tmp
        bne @quiet
        rts

        .include "speaker.inc"
