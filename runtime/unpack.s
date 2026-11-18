; unpack.s - unpack a packed picture (format in tools/pack.py).
; Loads at $0300. The computer puts the packed bytes' address at $0303 and the
; destination at $0305, uploads it with the packed bytes, and ends the session
; with a jump here; it returns to the receiver with carry clear, so the boot
; loop starts listening again.
; Build: ca65 -I runtime runtime/unpack.s -o unpack.o
;        ld65 -C runtime/unpack.cfg unpack.o -o unpack.bin
;
; Control bytes:  $00-$7F  c+1 literal bytes follow
;                 $80-$BF  copy (c & $3F) + 2 bytes from o+1 back (one byte o)
;                 $C0-$FE  copy (c & $3F) + 3 bytes from hi:lo back (two bytes)
;                 $FF      end
; A copy runs forwards a byte at a time, so it can overlap what it writes.

src     = $06                   ; 2 bytes: next packed byte
dst     = $08                   ; 2 bytes: next byte to write
from    = $0A                   ; 2 bytes: where a copy reads
dist    = $0C                   ; 2 bytes: a copy's distance back

        .segment "CODE"

        jmp start
srcp:   .word $6000             ; set by the computer
dstp:   .word $2000             ; set by the computer

start:  lda srcp
        sta src
        lda srcp+1
        sta src+1
        lda dstp
        sta dst
        lda dstp+1
        sta dst+1

next:   ldy #0
        lda (src),y             ; control byte
        jsr step
        cmp #$FF
        beq done
        cmp #$80
        bcc literal
        tax                     ; X = control byte
        lda (src),y             ; distance, low byte (Y is still 0)
        jsr step
        cpx #$C0
        bcs far
        sta dist                ; near: o+1 back, so from = dst - o - 1
        clc
        lda dst
        sbc dist
        sta from
        lda dst+1
        sbc #0
        sta from+1
        txa
        and #$3F
        clc
        adc #2
        bne copy                ; always: 2-65
far:    sta dist
        lda (src),y             ; distance, high byte
        jsr step
        sta dist+1
        sec                     ; from = dst - distance
        lda dst
        sbc dist
        sta from
        lda dst+1
        sbc dist+1
        sta from+1
        txa
        and #$3F
        clc
        adc #3                  ; 3-65
copy:   tax
@c:     lda (from),y
        sta (dst),y
        iny
        dex
        bne @c
        beq advdst              ; always

literal:
        tax
        inx                     ; 1-128 bytes
@l:     lda (src),y
        sta (dst),y
        iny
        dex
        bne @l
        tya                     ; src += Y
        clc
        adc src
        sta src
        bcc advdst
        inc src+1
advdst: tya                     ; dst += Y
        clc
        adc dst
        sta dst
        bcc next
        inc dst+1
        bcs next                ; always (carry set by the add)

done:   clc
        rts

; past one packed byte; keeps A and Y
step:   inc src
        bne @s
        inc src+1
@s:     rts
