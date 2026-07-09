// Tests for the round/rest pace math: countPunches, computeGapAfterSpeech,
// intervalForCombo, PACE_GAP band ordering, and tplDuration.

suite('countPunches', () => {
  test('counts digits 1-6 in a plain combo', () => {
    assertEqual(countPunches('1-2-3'), 3);
    assertEqual(countPunches('1-1-2-3-6-3'), 6);
  });
  test('counts only the punch, not the modifier phrase', () => {
    assertEqual(countPunches('2 to the body'), 1);
    assertEqual(countPunches('slip inside, 2-3'), 2);
  });
  test('falls back to 1 when there are no digits at all', () => {
    assertEqual(countPunches('circle right'), 1);
  });
});

suite('PACE_GAP band ordering', () => {
  test('min and max both increase monotonically from rapid -> slow', () => {
    const order = ['rapid', 'fast', 'normal', 'slow'];
    for (let i = 1; i < order.length; i++) {
      const prev = PACE_GAP[order[i - 1]], cur = PACE_GAP[order[i]];
      assert(prev.min < cur.min, `${order[i-1]}.min should be < ${order[i]}.min`);
      assert(prev.max < cur.max, `${order[i-1]}.max should be < ${order[i]}.max`);
    }
  });
});

suite('computeGapAfterSpeech', () => {
  test('honors an explicit paceOverrideSec regardless of other params', () => {
    const r = { paceOverrideSec: 7, type: 'speed', intensity: 50 };
    assertEqual(computeGapAfterSpeech('1-2', r), 7);
  });

  test('higher intensity means a shorter gap, holding everything else equal', () => {
    const lowIntensity = computeGapAfterSpeech('1-2', { type: 'speed', intensity: 0 });
    const highIntensity = computeGapAfterSpeech('1-2', { type: 'speed', intensity: 100 });
    assert(highIntensity < lowIntensity, `expected high-intensity gap (${highIntensity}) < low-intensity gap (${lowIntensity})`);
  });

  test('matches hand-computed value for a simple 2-punch combo at 100% intensity', () => {
    // speed round -> pace 'rapid' -> {min:1.5, max:3}; punches=2 -> execTime=1.1
    // recoveryGap at intensity=100 = max - (max-min)*1 = min = 1.5 -> total 2.6
    const gap = computeGapAfterSpeech('1-2', { type: 'speed', intensity: 100 });
    assertClose(gap, 2.6, 0.001);
  });

  test('a slower pace (power/warmup/technical) gives a longer gap than a rapid one at the same intensity', () => {
    const rapidGap = computeGapAfterSpeech('1-2', { type: 'speed', intensity: 60 });
    const slowGap = computeGapAfterSpeech('1-2', { type: 'power', intensity: 60 });
    assert(slowGap > rapidGap, `expected power (slow pace) gap ${slowGap} > speed (rapid pace) gap ${rapidGap}`);
  });

  test('never returns less than the 1.5s floor', () => {
    const gap = computeGapAfterSpeech('1', { type: 'speed', intensity: 100, paceMult: 0.01 });
    assert(gap >= 1.5, `expected floor of 1.5, got ${gap}`);
  });
});

suite('intervalForCombo', () => {
  test('honors an explicit overrideSec', () => {
    assertEqual(intervalForCombo('1-2', 50, 'normal', 1, 9.5), 9.5);
  });
  test('never returns less than the 1.8s floor', () => {
    const gap = intervalForCombo('1', 100, 'rapid', 0.1);
    assertEqual(gap, 1.8);
  });
  test('more punches means more throw time, so a longer combo gets a longer interval at the same settings', () => {
    const shortGap = intervalForCombo('1-2', 50, 'normal', 1);
    const longGap = intervalForCombo('1-2-3-2-3-2', 50, 'normal', 1);
    assert(longGap > shortGap, `expected 6-punch combo interval ${longGap} > 2-punch interval ${shortGap}`);
  });
});

suite('tplDuration', () => {
  test('sums work+rest across every round and converts to minutes', () => {
    const tpl = { seq: [{ work: 120, rest: 45 }, { work: 150, rest: 0 }] };
    // (120+45+150+0) / 60 = 5.25 -> rounds to 5
    assertEqual(tplDuration(tpl), 5);
  });
});
