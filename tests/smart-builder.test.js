// Tests for parseSmartSession -- the pure logic behind "Build Me One".
// buildSmartSession() itself is just DOM glue around this function.

suite('parseSmartSession: parsing the free-text description', () => {
  test('reads an explicit minute count out of the text', () => {
    const r = parseSmartSession('30 min, sore today, keep it technical', 45, []);
    assertEqual(r.targetMin, 30);
    assertEqual(r.isSore, true);
    assertEqual(r.isTechnical, true);
  });

  test('falls back to the passed-in duration when no minutes are mentioned', () => {
    const r = parseSmartSession('sore today', 45, []);
    assertEqual(r.targetMin, 45);
  });

  test('every plan starts with warmup and ends with finisher, regardless of input', () => {
    const inputs = ['30 min jab work', '10 min sore', '60 min fight camp pressure southpaw'];
    inputs.forEach(text => {
      const r = parseSmartSession(text, 30, []);
      assertEqual(r.plan[0].type, 'warmup', `first round for "${text}"`);
      assertEqual(r.plan[r.plan.length - 1].type, 'finisher', `last round for "${text}"`);
    });
  });

  test('"jab" keyword produces at least two jab rounds when there is enough time to fit them', () => {
    const r = parseSmartSession('45 min, jab', 45, []);
    const jabRounds = r.plan.filter(x => x.type === 'jab');
    assert(jabRounds.length >= 2, `expected >= 2 jab rounds, got ${jabRounds.length}`);
  });

  test('total planned session length is roughly the requested duration', () => {
    const r = parseSmartSession('20 min, technical', 20, []);
    const totalMin = r.plan.reduce((a, x) => a + x.work + x.rest, 0) / 60;
    assertClose(totalMin, 20, 3, `expected ~20 min, got ${totalMin.toFixed(1)}`);
  });
});

suite('parseSmartSession: conflict resolution (sore + conditioning)', () => {
  test('sore + conditioning together triggers the light-conditioning path (short rests)', () => {
    const r = parseSmartSession('sore today but need some conditioning', 30, []);
    assertEqual(r.isSore, true);
    assertEqual(r.isConditioning, true);
    assertEqual(r.lightConditioning, true);
    const speedRound = r.plan.find(x => x.type === 'speed');
    assert(speedRound, 'expected a speed round to be included alongside conditioning');
    assertEqual(speedRound.rest, 25, 'light-conditioning rest should be the short 25s band');
    assertEqual(speedRound.intensity, 45, 'sore caps intensity at 45 even in the light-conditioning path');
  });
});

suite('parseSmartSession: progressive overload', () => {
  test('3 straight complete+sharp sessions bump intensity (capped at 90), reflected in a plain round', () => {
    const sharpHistory = [
      { complete: 'yes', feel: 'sharp' },
      { complete: 'yes', feel: 'sharp' },
      { complete: 'yes', feel: 'sharp' },
    ];
    const r = parseSmartSession('just a normal session', 30, sharpHistory);
    assertEqual(r.overload, true);
    // with no keywords matched, base intensity is 65, bumped to 71 by overload
    const flowRound = r.plan.find(x => x.type === 'flow');
    assert(flowRound, 'expected a flow round in the default fallback sequence');
    assertEqual(flowRound.intensity, 71);
  });

  test('overload does NOT apply when the description says sore', () => {
    const sharpHistory = [
      { complete: 'yes', feel: 'sharp' },
      { complete: 'yes', feel: 'sharp' },
      { complete: 'yes', feel: 'sharp' },
    ];
    const r = parseSmartSession('sore today', 30, sharpHistory);
    assertEqual(r.overload, true); // the flag itself is still computed...
    assertEqual(r.isSore, true);
    // ...but isSore short-circuits intensity to 45 regardless of overload.
    const warmupRound = r.plan.find(x => x.type === 'warmup');
    assert(warmupRound.intensity <= 45, 'sore should keep intensity low even with an overload streak');
  });

  test('does not trigger with fewer than 3 qualifying sessions', () => {
    const shortHistory = [{ complete: 'yes', feel: 'sharp' }, { complete: 'yes', feel: 'sharp' }];
    const r = parseSmartSession('just a normal session', 30, shortHistory);
    assertEqual(r.overload, false);
  });
});
