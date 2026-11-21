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
;              A frame start that changes page first shows the page the frame
;              before was drawn on (at the next vertical blank, see vsync), so a
;              --flip stream draws every frame on the hidden page and shows it
;              once it's complete, and a frame start on the same page carries on
;              finishing it unseen; a stream that only sends $FE draws on the
;              page being shown, as before page 2 existed.
;   $01-$7E    literal run: n data bytes follow, written at the current address
;   $7F n c    audio burst: play n pages (n*64 ms) from RAM, then carry on;
;              c = n xor $55 (a stray $7F in damaged data rarely passes this)
;   $7F n c    with c = n xor $2A, right after a frame start that changes page:
;              a fast block (below) of 2n bytes follows, the last one $A5. The
;              player stores it while it waits for the vertical blank, shows the
;              finished page, then draws the block, so the wait doesn't waste
;              the line. A block that doesn't end in $A5 lost or gained a byte:
;              it isn't drawn, and the player discards until the next mark
;   $7F l c h  with c = l xor $3C: draw the fast block preloaded at
;              $6000 + 4 * (l + 128 * h), then carry on with the line
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
; Fast blocks are drawn from memory, about four times faster than the line
; brings bytes, and use their own format: entries of s, e, then e-2 screen
; bytes, palette bit included. Each entry moves on s bytes from where the last
; entry's bytes started (from the frame's current address, less 2, for the
; first) and writes its bytes there; e = 1 or 2 only moves on; e = 0 ends the
; block, setting the current address to where it moved to plus 2. The host
; keeps the line quiet while a block is drawn (tools/a2hdr.py fast_cycles).
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
FBUF    = $1300                 ; where a fast block after a page change is stored
PRELOAD = $60                   ; preloaded fast blocks start at $6000
QN      = 11                    ; vertical-blank wait: 11 x 41 cycles with no MARK
SENT    = $A5                   ; the last byte of a fast block after a page change

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
blim    = $17                   ; a fast block stops at this page
want    = $18                   ; bytes in the fast block being received
q       = $1C                   ; 2 bytes: fast block screen address, less 2
bp      = $1E                   ; 2 bytes: fast block read pointer

        .export lost, ctl, vframe, vframe2, fast, fblock, vsync
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

vop:    jsr getb
vdisp:  beq vop                 ; $00: ignored
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
        beq clong
        cmp #$FE
        beq vframe
        cmp #$FB
        beq vframe2
        cmp #$80
        bne cskip
        jmp endop               ; (out of branch range)
vframe: lda #$20                ; $FE: draw on page 1, from its top
        sta ptr+1
        lda #$40
        sta top
        lda #<LOWSCR
        bne newpg               ; always
vframe2:
        lda #$40                ; $FB: draw on page 2
        sta ptr+1
        lda #$60
        sta top
        lda #<HISCR
newpg:  ldy #0
        sty ptr
        sty pal                 ; each frame starts with the palette bit off
        cmp sw                  ; the same page as the last frame: carry on
        sta sw                  ; drawing it, unseen
        bne @new
        jmp vop                 ; (out of branch range)
@new:   jmp flip                ; a new page: show the finished one first
cskip:  cmp #$FC
        bcs cpal
        and #$7F                ; skip 1-122
        clc
        adc ptr
        sta ptr
        bcc chk
        inc ptr+1
        jmp chk
cpal:   lsr a                   ; $FC -> carry 0, $FD -> carry 1
        lda #0
        ror a                   ; ... -> $00 or $80
        sta pal
        jmp vop
clong:  jsr getb
        bmi ctl
        sta tmp                 ; lo (7 bits)
        jsr getb
        bmi ctl
        lsr a                   ; hi (7 bits): 128*hi = (hi>>1) high byte + (hi&1) << 7
        sta tmp2
        lda tmp
        bcc cn
        ora #$80
cn:     clc
        adc ptr
        sta ptr
        lda ptr+1
        adc tmp2
        sta ptr+1
        jmp chk


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
quit:   bit KBDSTRB
        bit TXTSET
        bit LOWSCR
        plp                     ; the php from start
        rts

        .include "speaker.inc"

; ---------------------------------------------------------------------------
; The rest of the player, after the speaker routine ($1080 up)
; ---------------------------------------------------------------------------
        .segment "CODE2"

; $7F: an audio burst (n, n xor $55) or a preloaded fast block (l, l xor $3C, h)
burst:  jsr getb                ; page count, or the block's address (low part)
        bmi ctl2
        sta apages
        jsr getb                ; check byte
        bmi ctl2
        eor apages
        cmp #$3C
        beq preop
        cmp #$55
        bne lost2               ; neither: not a real op
        lda apages
bplay:  sta apages              ; (Z set for A)
        bne @go
        jmp vop                 ; 0 pages: nothing to do
@go:    clc
        adc aptr+1
        bcs lost2               ; wrapped past $FF00: certainly bad
        ldy aptr+1
        cpy #$C1
        bcs @play               ; already in the card: the wrap check was enough
        cmp #$F0                ; from main RAM: $C000 up to $EF00 means $D000 up to $FF00
        bcs lost2
@play:  jsr splay
        ldx #SLOTX              ; play uses X
        jmp vop

ctl2:   jmp ctl                 ; (out of branch range from here)
lost2:  jmp lost

preop:  jsr getb                ; h: the block is at $6000 + 4 * (l + 128 * h)
        bmi ctl2
        sta tmp
        lda apages
        asl a
        asl a                   ; carry = bit 6 of l
        sta bp
        lda tmp
        rol a                   ; 2h + that bit; h < $80, so carry is now 0
        adc #PRELOAD
        sta bp+1
        cmp #$C0                ; blocks are in main RAM, below $C000
        bcs lost2
        lda #$C0
        sta blim
        jsr fast
        jmp chk

; A frame start on a new page: the page drawn last (shown by sw xor 1) is
; complete, so show it in the next vertical blank. Normally a fast block
; follows, received while waiting; anything else gets the plain wait.
flip:   jsr getb
        cmp #$7F
        bne @plain
        jsr getb
        bmi ctl2
        sta tmp
        jsr getb
        bmi ctl2
        eor tmp
        cmp #$2A
        bne @nob
        lda tmp
        bne fblock
        beq lost2               ; (always) an empty block isn't sent
@nob:   cmp #$55
        bne lost2
        lda tmp                 ; a burst: show the page, then play it
        pha
        jsr show
        pla
        jmp bplay
@plain: pha
        jsr show
        pla
        jmp vdisp

show:   jsr vsync
        lda sw
        lsr a                   ; carry = 1 while drawing page 2 (HISCR is odd)
        bcs @p1
        bit HISCR
        rts
@p1:    bit LOWSCR
        rts

; Receive a 2n-byte fast block into FBUF while waiting for the vertical blank
; as vsync does (below), flip, then draw it. Each pass polls the serial card,
; then reads the floating bus three times 8 cycles apart: 41 cycles (51 with
; a byte), and a restart goes straight back to the poll, so polls are never
; more than 57 cycles apart (a byte comes every 88.6 at 115200). The host
; sends the block's bytes straight after the op, for longer than the wait can
; take (19 ms), then stays quiet while it's drawn.
fblock: asl a
        sta want
        ldy #0                  ; bytes received
        sty tmp2                ; restarts left: 256
@quiet: lda #QN
        sta tmp
@q:     lda ACIA_STAT,x
        and #$08
        beq @n1
        lda ACIA_DATA,x
        sta FBUF,y
        iny
@n1:    lda HIRES
        cmp #MARK
        beq @again
        lda HIRES
        cmp #MARK
        beq @again
        lda HIRES
        cmp #MARK
        beq @again
        dec tmp
        bne @q
@w:     lda ACIA_STAT,x         ; then the first MARK (256 passes at most)
        and #$08
        beq @n2
        lda ACIA_DATA,x
        sta FBUF,y
        iny
@n2:    lda HIRES
        cmp #MARK
        beq @vbl
        lda HIRES
        cmp #MARK
        beq @vbl
        lda HIRES
        cmp #MARK
        beq @vbl
        dec tmp
        bne @w
        beq @vbl                ; (always) no MARK: flip anyway
@again: dec tmp2                ; MARK too soon: start again (this takes the
        bne @quiet              ; rest of the 8-cycle hole window, so once a line)
@vbl:   lda sw
        lsr a
        bcs @s1
        bit HISCR
        bcc @fin                ; (always)
@s1:    bit LOWSCR
@fin:   cpy want                ; the rest of the block
        beq @run
        lda KBD
        bmi @key
        lda ACIA_STAT,x
        and #$08
        beq @fin
        lda ACIA_DATA,x
        sta FBUF,y
        iny
        bne @fin                ; (always: want <= 254)
@key:   jmp quit
@bad:   jmp lost
@run:   lda FBUF-1,y            ; the block's last byte must be SENT
        cmp #SENT
        bne @bad
        lda #<FBUF
        sta bp
        lda #>FBUF
        sta bp+1
        lda #>FBUF+1
        sta blim
        jsr fast
        jmp chk

        .segment "FAST"         ; its own page: no branch crosses a page, so the
                                ; cycle count is the same wherever the code moves
; Draw the fast block at bp (format at the top). It may read up to the page
; before blim, and never writes outside the page being drawn. The cycles are
; counted in tools/a2hdr.py fast_cycles: keep them in step. Uses A, Y, cnt, tmp2.
fast:   sec                     ; q = ptr - 2
        lda ptr
        sbc #2
        sta q
        lda ptr+1
        sbc #0
        sta q+1
        ldy top
        dey
        sty tmp2                ; the last page of the screen being drawn
fent:   ldy #0
        lda (bp),y              ; s
        clc
        adc q
        sta q
        bcc @1
        inc q+1
@1:     iny
        lda (bp),y              ; e
        cmp #3
        bcc fspec
        sta cnt
        lda q+1
        cmp tmp2
        bcs fedge               ; on the screen's last page, or past it
fcopy:  iny
@l:     lda (bp),y
        sta (q),y
        iny
        cpy cnt
        bne @l
fnext:  tya                     ; on to the next entry
        clc
        adc bp
        sta bp
        bcc fent
        inc bp+1
        lda bp+1
        cmp blim
        bcc fent
        bcs fend                ; (always) ran out of block: stop
fspec:  ldy #2                  ; e < 3
        cmp #0
        bne fnext               ; 1 or 2: just moved on
fend:   clc                     ; 0: the end. ptr = q + 2
        lda q
        adc #2
        sta ptr
        lda q+1
        adc #0
        sta ptr+1
        rts
fedge:  bne fbad                ; past the screen's last page
        lda q                   ; on it: the bytes must end by its last byte
        clc
        adc cnt                 ; q + e = 1 + the last address written, + 2
        bcc fcopy
        beq fcopy               ; exactly at the end
fbad:   pla                     ; drop the return address
        pla
        jmp lost

; wait for the start of the vertical blank (II, II+ and IIe alike). Reading a
; soft switch returns the byte the video circuit last read (the floating bus).
; In hi-res it reads the screen holes, which hold MARK and are never shown,
; only during the top third of the screen and the vertical blank. So: wait
; until about 6 lines go by without MARK (the beam is in the lower two
; thirds), and the next MARK is the start of the blank. Takes up to one screen
; (17,030 cycles); the host stays quiet meanwhile. Gives up after 200 restarts
; or about 230 lines of waiting (no floating bus, an accelerator), so it never
; hangs. Uses A, Y and tmp; X (the slot) is kept. Only used when a page change
; comes without a fast block (fblock does the same while receiving).
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
