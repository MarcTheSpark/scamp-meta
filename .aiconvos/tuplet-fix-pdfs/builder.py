import sys, abjad
from scamp import Session

# Each bar is 5/8 = [1.0 filler][1.5 test beat]. The 1.5 beat holds a quintuplet (or septuplet)
# with the grouping under test (durations in quarter notes; a 1.5-beat quintuplet slot = 0.3).
q = 0.3          # quintuplet slot
groupings = [
    [3*q, 2*q],            # 3+2
    [2*q, 3*q],            # 2+3
    [1*q, 1*q, 3*q],       # 1+1+3
    [3*q, 1*q, 1*q],       # 3+1+1
    [1*q, 3*q, 1*q],       # 1+3+1
    [4*q, 1*q],            # 4+1
    [1*q, 4*q],            # 1+4
    [2*q, 2*q, 1*q],       # 2+2+1
    [2*q, 1*q, 2*q],       # 2+1+2
]

s = Session(); s.fast_forward()
inst = s.new_silent_part('rhythms')
s.start_transcribing()
barlines = []
t = 0.0
for g in groupings:
    inst.play_note(60, 0.8, 1.0, blocking=False); s.wait(1.0)   # filler quarter (beat 1)
    for d in g:
        inst.play_note(67, 0.8, d, blocking=False); s.wait(d)
    t += 2.5
    barlines.append(t)

score = s.stop_transcribing().to_score(bar_line_locations=barlines[:-1], max_divisor=14)
lf = score.to_abjad(wrap_as_file=True)
abjad.persist.as_pdf(lf, sys.argv[1])
print("rendered", sys.argv[1])
