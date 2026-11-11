; lcprobe.s - test all 16K of a language card (Microsoft RAMCard, etc).
; Loads at $1200. Runs from main RAM, calls no ROM while the card is switched in.
; Prints the result on the screen and sends it over the serial line, then waits
; for a key and returns to the receiver with carry clear. It overwrites everything
; in the card. See tools/lcprobe.py, which reads the result.
; Build: ca65 -I runtime runtime/lcprobe.s -o lcprobe.o
;        ld65 -C runtime/lcprobe.cfg lcprobe.o -o lcprobe.bin

        .include "config.inc"           ; SLOT

LC_R2W  = $C083                 ; read twice: read+write RAM, bank 2
LC_R1W  = $C08B                 ; read twice: read+write RAM, bank 1
LC_ROM  = $C082                 ; read ROM, no writes to the card
ROMTEST = $F800                 ; any ROM byte
DATA    = $C088 + SLOTX             ; the 6551, already set up by the receiver
STAT    = $C089 + SLOTX
KBD     = $C000
KBDSTRB = $C010
COUT    = $FDED

ptr     = $06                   ; 2 bytes
seed    = $08
endp    = $09
fail    = $0A                   ; failures in the current region (stops at 255)
msgp    = $0B                   ; 2 bytes

        .segment "CODE"

start:  lda ROMTEST
        sta rombyte
        php
        sei
        ldx #0                  ; X = pass: 0 = pattern, $FF = its inverse
pass:   lda LC_R2W
        lda LC_R2W
        txa
        sta seed
        lda #$D0
        ldy #$00                ; $D000-$FFFF, bank 2
        jsr fill
        lda LC_R1W
        lda LC_R1W
        txa
        eor #$55
        sta seed
        lda #$D0
        ldy #$E0                ; $D000-$DFFF, bank 1
        jsr fill

        lda #$D0                ; (seed still bank 1's)
        ldy #$E0
        jsr check
        jsr addfail1
        lda LC_R2W
        lda LC_R2W
        stx seed
        lda #$D0
        ldy #$E0
        jsr check
        jsr addfail2
        lda #$E0
        ldy #$00
        jsr check
        jsr addfail3
        cpx #$FF
        beq done
        ldx #$FF
        bne pass

done:   lda LC_ROM              ; back to ROM before calling anything
        plp

        ldx #0
        lda #<m_title
        ldy #>m_title
        jsr puts
        lda #<m_b2
        ldy #>m_b2
        jsr puts
        lda f2
        jsr okfail
        lda #<m_b1
        ldy #>m_b1
        jsr puts
        lda f1
        jsr okfail
        lda #<m_e0
        ldy #>m_e0
        jsr puts
        lda f3
        jsr okfail
        lda #<m_rom
        ldy #>m_rom
        jsr puts
        lda ROMTEST
        cmp rombyte
        beq @romok
        inc f4
@romok: lda f4
        jsr okfail

        lda f1
        ora f2
        ora f3
        ora f4
        bne @bad
        lda #<m_good
        ldy #>m_good
        jsr puts
        jmp finish
@bad:   lda #<m_badm
        ldy #>m_badm
        jsr puts
        lda badbank
        jsr hex
        lda #<m_at
        ldy #>m_at
        jsr puts
        lda badhi
        jsr hex
        lda badlo
        jsr hex
        lda #<m_got
        ldy #>m_got
        jsr puts
        lda badgot
        jsr hex
        lda #<m_want
        ldy #>m_want
        jsr puts
        lda badwant
        jsr hex
        lda #$0D
        jsr out

finish: lda #<m_end
        ldy #>m_end
        jsr puts
        bit KBDSTRB
@key:   lda KBD
        bpl @key
        bit KBDSTRB
        clc                     ; receiver and restart loop: listen again
        rts

; A = first page, Y = end page (exclusive; $00 means up to $FFFF). seed set.
fill:   sta ptr+1
        sty endp
        ldy #0
        sty ptr
@lp:    tya
        eor ptr+1
        eor seed
        sta (ptr),y
        iny
        bne @lp
        inc ptr+1
        lda ptr+1
        cmp endp
        bne @lp
        rts

; the same range and pattern, counting mismatches and keeping the first one
check:  sta ptr+1
        sty endp
        ldy #0
        sty ptr
        sty fail
@lp:    tya
        eor ptr+1
        eor seed
        cmp (ptr),y
        beq @ok
        pha
        lda fail
        cmp #$FF
        beq @full
        inc fail
@full:  lda badflag
        bne @seen
        inc badflag
        sty badlo
        lda ptr+1
        sta badhi
        lda (ptr),y
        sta badgot
        pla
        sta badwant
        lda bank
        sta badbank
        jmp @ok
@seen:  pla
@ok:    iny
        bne @lp
        inc ptr+1
        lda ptr+1
        cmp endp
        bne @lp
        rts

addfail1:
        lda fail
        ora f1
        sta f1
        lda #2                  ; the next regions tested are bank 2
        sta bank
        rts
addfail2:
        lda fail
        ora f2
        sta f2
        rts
addfail3:
        lda fail
        ora f3
        sta f3
        lda #1                  ; a second pass starts with bank 1 again
        sta bank
        lda #$55                ; and its bank 1 seed is set by pass
        rts

okfail: bne @f
        lda #<m_ok
        ldy #>m_ok
        jmp puts
@f:     lda #<m_fail
        ldy #>m_fail
        jmp puts

puts:   sta msgp
        sty msgp+1
        ldy #0
@lp:    lda (msgp),y
        beq @end
        sty ysave
        jsr out
        ldy ysave
        iny
        bne @lp
@end:   rts

hex:    pha
        lsr
        lsr
        lsr
        lsr
        jsr nib
        pla
        and #$0F
nib:    cmp #10
        bcc @d
        adc #6                  ; carry is set: +7
@d:     adc #$30
        ; fall through
out:    pha
@w:     lda STAT
        and #$10
        beq @w
        pla
        sta DATA
        ora #$80
        jmp COUT

rombyte: .byte 0
badflag: .byte 0
badlo:   .byte 0
badhi:   .byte 0
badgot:  .byte 0
badwant: .byte 0
badbank: .byte 0
bank:    .byte 1
ysave:   .byte 0
f1:      .byte 0
f2:      .byte 0
f3:      .byte 0
f4:      .byte 0

m_title: .byte $0D, "LANGUAGE CARD PROBE", $0D, 0
m_b2:    .byte "D000 BANK 2: ", 0
m_b1:    .byte "D000 BANK 1: ", 0
m_e0:    .byte "E000-FFFF:   ", 0
m_rom:   .byte "ROM BACK:    ", 0
m_ok:    .byte "OK", $0D, 0
m_fail:  .byte "FAIL", $0D, 0
m_good:  .byte "RESULT: 16K GOOD", $0D, 0
m_badm:  .byte "RESULT: FAIL BANK ", 0
m_at:    .byte " AT ", 0
m_got:   .byte " GOT ", 0
m_want:  .byte " WANT ", 0
m_end:   .byte "ANY KEY", $0D, 0
