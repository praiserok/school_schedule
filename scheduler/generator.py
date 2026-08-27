"""
Generator: OR-Tools CP-SAT.

Model: one base week + alternating lessons.
  xb[a_i, d, p]  -- lesson in BOTH weeks (repeats every week)
  xa[a_i, d, p]  -- lesson in WEEK A only (0.5h part)
  xb2[a_i, d, p] -- lesson in WEEK B only (0.5h part)

For assignment a with hours_per_week = H:
  base_count[a] = floor(H)  -- same slots both weeks
  alt_count[a]  = 1 if H%1  -- one alternating lesson (either week A or B)

Two different 0.5h subjects of the same class can share the same slot:
  week A: subject P1 (xa=1), week B: subject P2 (xb2=1) -- slot always filled.

Constraints use:
  Week A occupancy: xb + xa
  Week B occupancy: xb + xb2

Saving:
  xb=1  --> Lesson(week=0) + Lesson(week=1) same slot
  xa=1  --> Lesson(week=0) only
  xb2=1 --> Lesson(week=1) only
"""
from collections import Counter, defaultdict
from itertools import combinations
from math import ceil
from ortools.sat.python import cp_model

from .models import Schedule, Lesson, Teacher, SchoolClass, TeacherSubject, Room


def _avail_mask(teacher, D: int) -> str:
    """Return a D-character availability mask for a teacher ('1'=available, '0'=not)."""
    return teacher.available_days[:D].ljust(D, '0')


def _blocked_slots(teacher, D: int, P: int) -> set:
    """Return set of (day, period) pairs blocked for this teacher."""
    if not teacher.unavailable_slots:
        return set()
    result = set()
    for day_str, periods in teacher.unavailable_slots.items():
        d = int(day_str)
        if d >= D:
            continue
        for p in periods:
            if p < P:
                result.add((d, p))
    return result


def _summary(schedule, assignments, base_count, alt_count, canonical,
             class_total_A, teachers, teacher_assignments, classes, D, P) -> str:
    cls_by_pk = {c.pk: c for c in classes}
    lines = [f'[{D} dn/tyj, {P} ur/den]']
    lines.append('Klasy (urokiv u tyzhni A):')
    for cls_pk, total in sorted(class_total_A.items()):
        cls = cls_by_pk.get(cls_pk)
        if cls is None:
            continue
        avg = f'{total/D:.1f}'
        lines.append(f'  {cls}: {total} ur/tyj (~{avg}/den, max {P})')
    lines.append('Vchyteli (urokiv u tyzhni A / limit):')
    for t in teachers:
        t_total = sum(base_count[a_i] + alt_count[a_i] for a_i in teacher_assignments[t.pk])
        if t_total == 0:
            continue
        mask = _avail_mask(t, D)
        avail = mask.count('1')
        lim = avail * t.max_lessons_per_day
        lines.append(f'  {t}: {t_total}/{lim} ({avail} dn x {t.max_lessons_per_day} ur)')
    return '\n'.join(lines)


def _diagnose(schedule, assignments, base_count, alt_count, canonical,
              class_total_A, teachers, teacher_assignments, classes, rooms,
              specialized_capacity, D, P) -> list:
    issues = []
    total_slots = D * P

    for c in classes:
        total = class_total_A[c.pk]
        if total > total_slots:
            issues.append(
                f'Клас {c}: {total} уроків/тиж > {total_slots} слотів ({D} дн x {P} ур)'
            )

    for t in teachers:
        mask = _avail_mask(t, D)
        avail_days = mask.count('1')
        max_possible = avail_days * t.max_lessons_per_day
        t_total = sum(base_count[a_i] + alt_count[a_i] for a_i in teacher_assignments[t.pk])
        if t_total > max_possible:
            issues.append(
                f'Вчитель {t}: {t_total} уроків/тиж > максимум '
                f'{max_possible} ({avail_days} дн x {t.max_lessons_per_day} ур/день)'
            )

    for t in teachers:
        mask = _avail_mask(t, D)
        if '1' not in mask and teacher_assignments[t.pk]:
            names = ', '.join(str(assignments[a_i]) for a_i in teacher_assignments[t.pk][:3])
            issues.append(f'Вчитель {t}: немає доступних днів, але є навантаження ({names}...)')

    for c in classes:
        sc = canonical[c.pk]
        if not sc:
            continue
        hi = ceil(class_total_A[c.pk] / D)
        if hi > P:
            issues.append(
                f'Клас {c}: потрібно мінімум {hi} ур/день, але лише {P} слотів/день'
            )

    for subj_id, cap in specialized_capacity.items():
        demand_ais = [a_i for a_i, a in enumerate(assignments) if a.subject_id == subj_id]
        if not demand_ais:
            continue
        subj_obj = assignments[demand_ais[0]].subject
        effective_cap = cap if subj_obj.allow_shared_room else sum(
            1 for r in rooms if r.subject_id == subj_id
        )
        total_lessons = sum(base_count[a_i] + alt_count[a_i] for a_i in demand_ais)
        min_slots_needed = ceil(total_lessons / effective_cap)
        if min_slots_needed > total_slots:
            n_classes = len({assignments[a_i].school_class_id for a_i in demand_ais})
            issues.append(
                f'Предмет {subj_obj.name}: {n_classes} класів, {total_lessons} ур/тиж, '
                f'лише {effective_cap} кабінет(ів) -- потрібно {min_slots_needed} > {total_slots} слотів'
            )

    teacher_by_pk = {t.pk: t for t in teachers}
    group_map: dict = defaultdict(dict)
    for a_i, a in enumerate(assignments):
        if a.group is not None:
            group_map[(a.school_class_id, a.subject_id)][a.group] = a_i
    for (cls_pk, subj_pk), gmap in group_map.items():
        group_ais = list(gmap.values())
        if len(group_ais) < 2:
            continue
        teacher_ids = {assignments[a_i].teacher_id for a_i in group_ais}
        if len(teacher_ids) <= 1:
            continue
        rep_a = assignments[group_ais[0]]
        for t_id in teacher_ids:
            t_obj = teacher_by_pk[t_id]
            t_total = sum(base_count[a_i] + alt_count[a_i] for a_i in teacher_assignments[t_id])
            mask = _avail_mask(t_obj, D)
            avail = mask.count('1') * t_obj.max_lessons_per_day
            if t_total > avail:
                issues.append(
                    f'Вчитель {t_obj} (co-scheduling {rep_a.subject}/{rep_a.school_class}): '
                    f'перевантажений ({t_total} > {avail} ур/тиж)'
                )

    # Check: teacher has more lessons of a non-double subject than available days
    for a_i, a in enumerate(assignments):
        if a.subject.can_be_double:
            continue
        total = base_count[a_i] + alt_count[a_i]
        if total <= 1:
            continue
        mask = _avail_mask(a.teacher, D)
        avail_days = mask.count('1')
        if total > avail_days:
            issues.append(
                f'Вчитель {a.teacher}: {a.subject} у {a.school_class} — '
                f'{total} ур/тиж, але лише {avail_days} робочих дн і подвійний урок заборонено '
                f'(потрібно мінімум {total} дн)'
            )

    # Check cross-teacher groups share at least one common available day
    for (cls_pk, subj_pk), gmap in group_map.items():
        group_ais = list(gmap.values())
        if len(group_ais) < 2:
            continue
        teacher_ids = list({assignments[a_i].teacher_id for a_i in group_ais})
        if len(teacher_ids) <= 1:
            continue
        t_objs = {t_id: teacher_by_pk[t_id] for t_id in teacher_ids}
        masks = {t_id: _avail_mask(t_objs[t_id], D) for t_id in teacher_ids}
        common_days = sum(
            1 for d in range(D) if all(masks[t_id][d] == '1' for t_id in teacher_ids)
        )
        max_base = max(base_count[a_i] for a_i in group_ais)
        rep_a = assignments[group_ais[0]]
        t_names = ', '.join(str(t_objs[t_id]) for t_id in teacher_ids)
        if common_days == 0:
            issues.append(
                f'Групи {rep_a.subject}/{rep_a.school_class}: вчителі [{t_names}] '
                f'не мають жодного спільного дня — co-scheduling неможливий'
            )
        elif common_days * P < max_base:
            issues.append(
                f'Групи {rep_a.subject}/{rep_a.school_class}: лише {common_days} спільних '
                f'дн × {P} ур = {common_days * P} слотів < {max_base} потрібних уроків'
            )

    # Check group balance: each student group must have the same total hours per class
    # so that no-window and uniform-load constraints are symmetric.
    for c in classes:
        by_group: dict = defaultdict(int)
        for a_i in range(len(assignments)):
            a = assignments[a_i]
            if a.school_class_id != c.pk or a.group is None:
                continue
            by_group[a.group] += base_count[a_i] + alt_count[a_i]
        if len(by_group) < 2:
            continue
        totals = {g: h for g, h in by_group.items()}
        ref_g, ref_h = next(iter(totals.items()))
        for g, h in totals.items():
            if h != ref_h:
                issues.append(
                    f'Клас {c}: дисбаланс груп — гр.{ref_g} має {ref_h} ур/тиж, '
                    f'гр.{g} має {h} ур/тиж. Виправте навантаження щоб суми збіглися.'
                )

    return issues


def _solve_gap_phase(assignments, class_assignments, teacher_assignments,
                     base_count, alt_count, classes, teachers, D, P, phase2_vals,
                     alt_pairs=None, specialized_capacity=None):
    """Phase 3: fix group lessons, re-optimize non-group lessons to minimize teacher windows.

    phase2_vals:           {a_i: {'base': [(d,p),...], 'xa': [(d,p),...], 'xb2': [(d,p),...]}}
    alt_pairs:             list of (a_x, a_y) — 0.5h subjects that share a slot opposite weeks.
    specialized_capacity:  {subj_id: cap} — max simultaneous PE-like lessons per slot.
    Returns same-format dict for non-group assignments only, or None if failed/no improvement.
    """
    ng_set = frozenset(a_i for a_i, a in enumerate(assignments) if a.group is None)
    if not ng_set:
        return None

    # Precompute fixed occupancy from Phase 2.
    # Fixed = group lessons + shared-room lessons (PE/gym).
    # Shared-room lessons are pinned to preserve the gym pairing that Phase 2
    # carefully built; re-optimizing them here breaks room assignments.
    fixed_t_A:   dict = defaultdict(bool)   # (t_pk, d, p) -> bool  week A
    fixed_t_B:   dict = defaultdict(bool)   # (t_pk, d, p) -> bool  week B
    fixed_cls_A: dict = defaultdict(bool)   # (cls_pk, d, p) -> bool
    fixed_cls_B: dict = defaultdict(bool)

    def _pin(a_i, a):
        """Pin an assignment as fixed (not re-optimized by Phase 3)."""
        for d, p in phase2_vals[a_i]['base']:
            fixed_t_A[a.teacher_id, d, p]        = True
            fixed_t_B[a.teacher_id, d, p]        = True
            fixed_cls_A[a.school_class_id, d, p] = True
            fixed_cls_B[a.school_class_id, d, p] = True
        for d, p in phase2_vals[a_i]['xa']:
            fixed_t_A[a.teacher_id, d, p]        = True
            fixed_cls_A[a.school_class_id, d, p] = True
        for d, p in phase2_vals[a_i]['xb2']:
            fixed_t_B[a.teacher_id, d, p]        = True
            fixed_cls_B[a.school_class_id, d, p] = True

    # Pin group lessons
    for a_i, a in enumerate(assignments):
        if a.group is None:
            continue
        _pin(a_i, a)

    # Pin shared-room (PE/gym) non-group lessons and remove them from ng_set.
    # This preserves the gym pairing from Phase 2 exactly.
    shared_room_ais = frozenset(
        a_i for a_i in ng_set
        if assignments[a_i].subject.allow_shared_room
    )
    for a_i in shared_room_ais:
        _pin(a_i, assignments[a_i])
    ng_set = ng_set - shared_room_ais

    # No gap optimization possible if no movable lesson has alternating weeks
    if not any(alt_count[a_i] for a_i in ng_set):
        return None

    model = cp_model.CpModel()
    xb3:  dict = {}
    xa3:  dict = {}
    xb23: dict = {}

    for a_i in ng_set:
        for d in range(D):
            for p in range(P):
                xb3[a_i, d, p] = model.new_bool_var(f'p3b_{a_i}_{d}_{p}')
                if alt_count[a_i]:
                    xa3[a_i, d, p]  = model.new_bool_var(f'p3a_{a_i}_{d}_{p}')
                    xb23[a_i, d, p] = model.new_bool_var(f'p3b2_{a_i}_{d}_{p}')
                else:
                    xa3[a_i, d, p]  = 0
                    xb23[a_i, d, p] = 0

    def vA(a_i, d, p):
        v = [xb3[a_i, d, p]]
        if alt_count[a_i]:
            v.append(xa3[a_i, d, p])
        return v

    def vB(a_i, d, p):
        v = [xb3[a_i, d, p]]
        if alt_count[a_i]:
            v.append(xb23[a_i, d, p])
        return v

    def _cum_max(tag, a, b):
        if isinstance(a, int) and isinstance(b, int):
            return max(a, b)
        if isinstance(a, int) and a == 1:
            return 1
        if isinstance(b, int) and b == 1:
            return 1
        if isinstance(a, int) and a == 0:
            return b
        if isinstance(b, int) and b == 0:
            return a
        v = model.new_bool_var(tag)
        model.add_max_equality(v, [a, b])
        return v

    # 1. Lesson counts
    for a_i in ng_set:
        model.add(sum(xb3[a_i, d, p] for d in range(D) for p in range(P)) == base_count[a_i])
        if alt_count[a_i]:
            model.add(sum(xa3[a_i, d, p] + xb23[a_i, d, p]
                          for d in range(D) for p in range(P)) == 1)
            for d in range(D):
                for p in range(P):
                    model.add(xa3[a_i, d, p] + xb23[a_i, d, p] <= 1)

    # 1b. Alt pairing: paired 0.5h subjects share the same slot with opposite weeks.
    for (a_x, a_y) in (alt_pairs or []):
        if a_x not in ng_set or a_y not in ng_set:
            continue
        for d in range(D):
            for p in range(P):
                if not isinstance(xa3[a_x, d, p], int) and not isinstance(xb23[a_y, d, p], int):
                    model.add(xa3[a_x, d, p] == xb23[a_y, d, p])

    # 2. Class conflict (non-group vs non-group + fixed group)
    for c in classes:
        ng_c = [a_i for a_i in class_assignments[c.pk] if a_i in ng_set]
        if not ng_c:
            continue
        for d in range(D):
            for p in range(P):
                fix_A = int(fixed_cls_A[c.pk, d, p])
                fix_B = int(fixed_cls_B[c.pk, d, p])
                model.add(cp_model.LinearExpr.Sum([v for a_i in ng_c for v in vA(a_i, d, p)]) + fix_A <= 1)
                model.add(cp_model.LinearExpr.Sum([v for a_i in ng_c for v in vB(a_i, d, p)]) + fix_B <= 1)

    # 3. Teacher conflict
    for t in teachers:
        ng_t = [a_i for a_i in teacher_assignments[t.pk] if a_i in ng_set]
        if not ng_t:
            continue
        for d in range(D):
            for p in range(P):
                fix_A = int(fixed_t_A[t.pk, d, p])
                fix_B = int(fixed_t_B[t.pk, d, p])
                model.add(cp_model.LinearExpr.Sum([v for a_i in ng_t for v in vA(a_i, d, p)]) + fix_A <= 1)
                model.add(cp_model.LinearExpr.Sum([v for a_i in ng_t for v in vB(a_i, d, p)]) + fix_B <= 1)

    # 4. Teacher availability (day + per-slot restrictions)
    for a_i in ng_set:
        mask    = _avail_mask(assignments[a_i].teacher, D)
        blocked = _blocked_slots(assignments[a_i].teacher, D, P)
        for d in range(D):
            for p in range(P):
                if mask[d] != '1' or (d, p) in blocked:
                    model.add(xb3[a_i, d, p] == 0)
                    if alt_count[a_i]:
                        model.add(xa3[a_i, d, p]  == 0)
                        model.add(xb23[a_i, d, p] == 0)

    # 5. Max lessons per day (fixed group + non-group <= max)
    for t in teachers:
        ng_t = [a_i for a_i in teacher_assignments[t.pk] if a_i in ng_set]
        if not ng_t:
            continue
        for d in range(D):
            fix_A = sum(int(fixed_t_A[t.pk, d, p]) for p in range(P))
            fix_B = sum(int(fixed_t_B[t.pk, d, p]) for p in range(P))
            ng_A = [v for a_i in ng_t for p in range(P) for v in vA(a_i, d, p)]
            ng_B = [v for a_i in ng_t for p in range(P) for v in vB(a_i, d, p)]
            model.add(cp_model.LinearExpr.Sum(ng_A) + fix_A <= t.max_lessons_per_day)
            model.add(cp_model.LinearExpr.Sum(ng_B) + fix_B <= t.max_lessons_per_day)

    # 6. Per-day lesson count: use Phase 2 actuals as reference (±1 flexibility).
    #    Count UNIQUE slots per day (not assignment entries) to avoid double-counting
    #    cross-teacher paired groups — g1 and g2 occupy the SAME (d,p) slot but are
    #    two separate assignments, so naively summing entries overcounts them.
    for c in classes:
        ng_c = [a_i for a_i in class_assignments[c.pk] if a_i in ng_set]
        if not ng_c:
            continue
        # Unique occupied slots per day (cross-teacher pairs share one slot → count once)
        slots_A: dict = defaultdict(set)
        slots_B: dict = defaultdict(set)
        for a_i in class_assignments[c.pk]:
            for d2, p2 in phase2_vals[a_i]['base']:
                slots_A[d2].add(p2)
                slots_B[d2].add(p2)
            for d2, p2 in phase2_vals[a_i]['xa']:
                slots_A[d2].add(p2)
            for d2, p2 in phase2_vals[a_i]['xb2']:
                slots_B[d2].add(p2)
        for d in range(D):
            fix_A = sum(int(fixed_cls_A[c.pk, d, p]) for p in range(P))
            fix_B = sum(int(fixed_cls_B[c.pk, d, p]) for p in range(P))
            ng_A = [v for a_i in ng_c for p in range(P) for v in vA(a_i, d, p)]
            ng_B = [v for a_i in ng_c for p in range(P) for v in vB(a_i, d, p)]
            tgt_A = max(0, len(slots_A[d]) - fix_A)
            tgt_B = max(0, len(slots_B[d]) - fix_B)
            if ng_A:
                model.add(cp_model.LinearExpr.Sum(ng_A) >= max(0, tgt_A - 1))
                model.add(cp_model.LinearExpr.Sum(ng_A) <= tgt_A + 1)
            if ng_B:
                model.add(cp_model.LinearExpr.Sum(ng_B) >= max(0, tgt_B - 1))
                model.add(cp_model.LinearExpr.Sum(ng_B) <= tgt_B + 1)

    # 7. No same-day repeats for non-double subjects
    for a_i in ng_set:
        a = assignments[a_i]
        if a.subject.can_be_double:
            continue
        total = base_count[a_i] + alt_count[a_i]
        if total <= 1:
            continue
        for d in range(D):
            model.add(cp_model.LinearExpr.Sum([v for p in range(P) for v in vA(a_i, d, p)]) <= 1)
            model.add(cp_model.LinearExpr.Sum([v for p in range(P) for v in vB(a_i, d, p)]) <= 1)

    # 8. No windows for students: combined (fixed group + non-group) occupancy must be
    #    consecutive from period 0. Phase 2 guarantees this for its solution, so
    #    Phase 3 is always FEASIBLE (Phase 2 values as hints satisfy this constraint).
    for c in classes:
        ng_c = [a_i for a_i in class_assignments[c.pk] if a_i in ng_set]
        if not ng_c:
            continue
        for d in range(D):
            for wk, fixed_cls, vfn in (('A', fixed_cls_A, vA), ('B', fixed_cls_B, vB)):
                # Build combined occupancy per period
                occ: list = []
                for p in range(P):
                    fix_p = int(fixed_cls[c.pk, d, p])
                    if fix_p:
                        occ.append(1)
                    else:
                        ng_p = [v for a_i in ng_c for v in vfn(a_i, d, p)]
                        if not ng_p:
                            occ.append(0)
                        else:
                            bv = model.new_bool_var(f'p3oc{wk}_{c.pk}_{d}_{p}')
                            model.add_max_equality(bv, ng_p)
                            occ.append(bv)

                # Backward cumulative max: after_max[p] = 1 if any lesson at period > p
                after_max: list = [None] * P
                after_max[P - 1] = 0
                for p in range(P - 2, -1, -1):
                    after_max[p] = _cum_max(f'p3am{wk}_{c.pk}_{d}_{p}', occ[p + 1], after_max[p + 1])

                # No-window: if anything after p, period p must be occupied
                for p in range(P - 1):
                    am = after_max[p]
                    o  = occ[p]
                    if isinstance(am, int) and am == 0:
                        continue   # nothing after p → no constraint
                    if isinstance(o, int) and o == 1:
                        continue   # already occupied
                    if isinstance(o, int) and o == 0:
                        # No lesson can go here; force nothing after either
                        if not (isinstance(am, int) and am == 0):
                            model.add(am == 0)
                        continue
                    # o is a BoolVar
                    if isinstance(am, int) and am == 1:
                        model.add(o == 1)
                    else:
                        model.add(o >= am)

    # Objective: minimize teacher windows (gaps) in combined schedule
    obj_gaps: list = []
    for t in teachers:
        ng_t  = [a_i for a_i in teacher_assignments[t.pk] if a_i in ng_set]
        t_mask = _avail_mask(t, D)
        for d in range(D):
            if t_mask[d] != '1':
                continue
            has_t: list = []
            for p in range(P):
                fix_p = int(fixed_t_A[t.pk, d, p])
                ng_p  = [v for a_i in ng_t for v in vA(a_i, d, p)]
                if fix_p:
                    has_t.append(1)
                elif ng_p:
                    bv = model.new_bool_var(f'p3th_{t.pk}_{d}_{p}')
                    model.add_max_equality(bv, ng_p)
                    has_t.append(bv)
                else:
                    has_t.append(0)

            if P < 3:
                continue

            occ_before: list = [None] * P
            occ_before[1] = has_t[0]
            for p in range(2, P):
                occ_before[p] = _cum_max(f'p3ob_{t.pk}_{d}_{p}', occ_before[p - 1], has_t[p - 1])

            occ_after: list = [None] * P
            occ_after[P - 2] = has_t[P - 1]
            for p in range(P - 3, -1, -1):
                occ_after[p] = _cum_max(f'p3oa_{t.pk}_{d}_{p}', occ_after[p + 1], has_t[p + 1])

            for p in range(1, P - 1):
                ht = has_t[p]
                ob = occ_before[p]
                oa = occ_after[p]
                if isinstance(ht, int) and ht == 1:
                    continue   # always occupied, no gap
                if isinstance(ob, int) and ob == 0:
                    continue   # nothing before
                if isinstance(oa, int) and oa == 0:
                    continue   # nothing after
                # gap = ob AND NOT ht AND oa
                not_ht = ht.Not() if not isinstance(ht, int) else (1 - ht)
                parts = [x for x in (ob, not_ht, oa)
                         if not (isinstance(x, int) and x == 1)]
                if any(isinstance(x, int) and x == 0 for x in parts):
                    continue   # gap always 0
                gap = model.new_bool_var(f'p3gap_{t.pk}_{d}_{p}')
                var_parts = [x for x in parts if not isinstance(x, int)]
                if var_parts:
                    model.add_min_equality(gap, var_parts)
                else:
                    model.add(gap == 1)
                obj_gaps.append(gap)

    if not obj_gaps:
        return None   # no gaps possible to optimize

    model.minimize(cp_model.LinearExpr.Sum(obj_gaps))

    # Hints from Phase 2 solution
    for a_i in ng_set:
        for d, p in phase2_vals[a_i]['base']:
            model.add_hint(xb3[a_i, d, p], 1)
        if alt_count[a_i]:
            for d, p in phase2_vals[a_i]['xa']:
                model.add_hint(xa3[a_i, d, p], 1)
            for d, p in phase2_vals[a_i]['xb2']:
                model.add_hint(xb23[a_i, d, p], 1)

    solver3 = cp_model.CpSolver()
    solver3.parameters.max_time_in_seconds = 30.0
    solver3.parameters.num_search_workers = 8
    solver3.parameters.log_search_progress = False
    status = solver3.solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    result: dict = {}
    for a_i in ng_set:
        base_s: list = []
        xa_s:   list = []
        xb2_s:  list = []
        for d in range(D):
            for p in range(P):
                if solver3.value(xb3[a_i, d, p]):
                    base_s.append((d, p))
                if alt_count[a_i]:
                    if solver3.value(xa3[a_i, d, p]):
                        xa_s.append((d, p))
                    if solver3.value(xb23[a_i, d, p]):
                        xb2_s.append((d, p))
        result[a_i] = {'base': base_s, 'xa': xa_s, 'xb2': xb2_s}
    return result


def _find_cross_subj_pairs(group_map, assignments, base_count, alt_count):
    """Find cross-subject pairing opportunities between same-teacher and cross-teacher subjects.

    Groups all same-teacher subjects by teacher, then matches each TEACHER GROUP (not
    individual subject) to a cross-teacher subject, prioritising the teacher with the
    most total hours (so the busiest same-teacher teacher gets paired first).

      Slot A: T teaches any g1 subject + cross_g2 teacher teaches g2
      Slot B: T teaches any g2 subject + cross_g1 teacher teaches g1

    When T's total hours differ from the cross-teacher subject's hours, partial pairing
    is used: the side with fewer slots gets an implication constraint (its slots are a
    subset of the partner's slots), rather than a full equality constraint.

    Returns:
      pairs: list of (s_g1_ais, s_g2_ais, c_g1, c_g2, mode)
             s_g1_ais / s_g2_ais — lists of all g1/g2 assignment indices for same-T teacher
             c_g1 / c_g2         — single assignment indices for cross-teacher subject
             mode                — 'equal' or 'partial'
      matched_cross_keys: set of (cls_pk, subj_pk) for matched cross-teacher subjects
      matched_same_keys:  set of (cls_pk, subj_pk) for matched same-teacher subjects
    """
    # Group by class
    cls_subjects: dict = defaultdict(dict)  # cls_pk -> {subj_pk: gmap}
    for (cls_pk, subj_pk), gmap in group_map.items():
        cls_subjects[cls_pk][subj_pk] = gmap

    pairs = []
    matched_cross_keys: set = set()
    matched_same_keys:  set = set()

    for cls_pk, subj_map in cls_subjects.items():
        # Group same-teacher subjects by teacher id: t_id → {'g1_ais': [...], 'g2_ais': [...]}
        same_by_teacher: dict = defaultdict(lambda: {'g1_ais': [], 'g2_ais': [], 'subj_pks': []})
        cross_teacher_subjs = []  # [(subj_pk, g1_ai, g2_ai, teacher_ids_set)]

        for subj_pk, gmap in subj_map.items():
            g_nums = sorted(gmap.keys())
            if len(g_nums) != 2:
                continue
            g1_ai = gmap[g_nums[0]]
            g2_ai = gmap[g_nums[1]]
            t1 = assignments[g1_ai].teacher_id
            t2 = assignments[g2_ai].teacher_id
            if t1 == t2:
                same_by_teacher[t1]['g1_ais'].append(g1_ai)
                same_by_teacher[t1]['g2_ais'].append(g2_ai)
                same_by_teacher[t1]['subj_pks'].append(subj_pk)
            else:
                cross_teacher_subjs.append((subj_pk, g1_ai, g2_ai, {t1, t2}))

        # Sort same-teacher groups by total hours descending (busiest teacher first)
        sorted_same = sorted(
            same_by_teacher.items(),
            key=lambda kv: sum(base_count[ai] + alt_count[ai] for ai in kv[1]['g1_ais']),
            reverse=True,
        )

        used_cross: set = set()
        matched_s_tids: set = set()  # teachers already matched as "s" side

        # Phase A: same-teacher ↔ cross-teacher pairing (priority)
        for t_id, tdata in sorted_same:
            g1_ais = tdata['g1_ais']
            g2_ais = tdata['g2_ais']
            t_base = sum(base_count[ai] for ai in g1_ais)
            t_alt  = sum(alt_count[ai]  for ai in g1_ais)

            for idx, (c_subj_pk, c_g1, c_g2, c_tids) in enumerate(cross_teacher_subjs):
                if idx in used_cross:
                    continue
                if (cls_pk, c_subj_pk) in matched_cross_keys:
                    continue
                # Teacher conflict: same-teacher must not be in cross-teacher's teacher set
                if t_id in c_tids:
                    continue

                c_base = base_count[c_g1]
                c_alt  = alt_count[c_g1]
                mode = 'equal' if (t_base == c_base and t_alt == c_alt) else 'partial'

                # c_is_cross=True: cross-teacher "c" side — apply remaining co-scheduled if needed
                pairs.append((g1_ais, g2_ais, c_g1, c_g2, mode, True))
                matched_cross_keys.add((cls_pk, c_subj_pk))
                for spk in tdata['subj_pks']:
                    matched_same_keys.add((cls_pk, spk))
                matched_s_tids.add(t_id)
                used_cross.add(idx)
                break  # each teacher group matches at most one cross-teacher subject

        # Phase B: same-teacher ↔ same-teacher pairing (when no cross-teacher partner found).
        # Pair the busiest unmatched teacher with the next busiest unmatched teacher.
        # Direction: busier teacher (s side) has more total hours → cross presence ⊆ s presence.
        # No "remaining co-scheduled" needed — the "c" teacher is same-teacher and never has
        # both g1 and g2 at the same slot (teacher conflict).
        used_c_tids: set = set()
        for t_id, tdata in sorted_same:
            if t_id in matched_s_tids:
                continue
            if t_id in used_c_tids:
                continue  # already used as "c" partner — don't re-pair as "s"
            g1_ais = tdata['g1_ais']
            g2_ais = tdata['g2_ais']
            t_base = sum(base_count[ai] for ai in g1_ais)
            t_alt  = sum(alt_count[ai]  for ai in g1_ais)

            # Find best unmatched partner with fewer-or-equal total hours
            best_partner = None
            best_c_base = 0
            for c_tid, cdata in sorted_same:
                if c_tid == t_id:
                    continue
                if c_tid in matched_s_tids:
                    continue
                if c_tid in used_c_tids:
                    continue
                c_base_c = sum(base_count[ai] for ai in cdata['g1_ais'])
                if t_base < c_base_c:
                    continue  # s side must be at least as busy as c side
                if c_base_c > best_c_base:
                    best_partner = (c_tid, cdata, c_base_c)
                    best_c_base = c_base_c

            if best_partner is None:
                continue

            c_tid, cdata, c_base_c = best_partner
            c_g1 = cdata['g1_ais'][0]
            c_g2 = cdata['g2_ais'][0]
            c_alt_c = sum(alt_count[ai] for ai in cdata['g1_ais'])
            mode = 'equal' if (t_base == c_base_c and t_alt == c_alt_c) else 'partial'

            # c_is_cross=False: same-teacher "c" side — no remaining co-scheduled
            pairs.append((g1_ais, g2_ais, c_g1, c_g2, mode, False))
            matched_s_tids.add(t_id)
            used_c_tids.add(c_tid)

    return pairs, matched_cross_keys, matched_same_keys


def _solve_group_phase(assignments, group_map, class_assignments, teacher_assignments,
                       base_count, alt_count, classes, teachers, D, P,
                       unpaired_edge: set | None = None,
                       cross_subj_pairs=None, matched_cross_keys=None) -> dict:
    """Phase 1: schedule group lessons only, maximising cross-teacher slot pairing.

    For each class, collects same-teacher group subjects (г1 and г2 taught by the
    same teacher) and adds a unidirectional constraint: the teacher with *fewer* group
    hours in that class is the "follower" — all their inner-period group slots must
    coincide with a group slot of the other teacher (cross-subject pairing).

    Falls back to basic feasibility (no pairing constraints) if pairing causes INFEASIBLE.
    When with_edge=True, forces unpaired same-teacher group lessons to be at period 0
    or the last occupied period of the day (not in the middle).

    Returns {a_i: {'base': [(d,p),...], 'xa': [(d,p),...], 'xb2': [(d,p),...]}}
    for group assignments only, to be used as hints in Phase 2.
    """
    grp_set = {a_i for gmap in group_map.values() for a_i in gmap.values()}
    if not grp_set:
        return {}
    edge_set = (unpaired_edge or set()) & grp_set

    def _build(with_pairing: bool, with_edge: bool = False):
        m = cp_model.CpModel()
        xb_: dict = {}; xa_: dict = {}; xb2_: dict = {}
        for a_i in grp_set:
            for d in range(D):
                for p in range(P):
                    xb_[a_i, d, p] = m.new_bool_var(f'g_xb_{a_i}_{d}_{p}')
                    if alt_count[a_i]:
                        xa_[a_i, d, p]  = m.new_bool_var(f'g_xa_{a_i}_{d}_{p}')
                        xb2_[a_i, d, p] = m.new_bool_var(f'g_xb2_{a_i}_{d}_{p}')
                    else:
                        xa_[a_i, d, p]  = 0
                        xb2_[a_i, d, p] = 0

        def vA(a_i, d, p):
            v = [xb_[a_i, d, p]]
            if alt_count[a_i]: v.append(xa_[a_i, d, p])
            return v

        def vB(a_i, d, p):
            v = [xb_[a_i, d, p]]
            if alt_count[a_i]: v.append(xb2_[a_i, d, p])
            return v

        # Lesson counts
        for a_i in grp_set:
            m.add(sum(xb_[a_i, d, p] for d in range(D) for p in range(P)) == base_count[a_i])
            if alt_count[a_i]:
                m.add(sum(xa_[a_i, d, p] + xb2_[a_i, d, p]
                          for d in range(D) for p in range(P)) == 1)
                for d in range(D):
                    for p in range(P):
                        m.add(xa_[a_i, d, p] + xb2_[a_i, d, p] <= 1)

        # Class conflict (within each group number)
        for c in classes:
            grp: dict = defaultdict(list)
            for a_i in class_assignments[c.pk]:
                if a_i in grp_set:
                    grp[assignments[a_i].group].append(a_i)
            for d in range(D):
                for p in range(P):
                    for vfn in (vA, vB):
                        for g_ais in grp.values():
                            m.add(cp_model.LinearExpr.Sum(
                                [v for a_i in g_ais for v in vfn(a_i, d, p)]) <= 1)

        # Teacher conflict
        for t in teachers:
            t_grp = [a_i for a_i in teacher_assignments[t.pk] if a_i in grp_set]
            if not t_grp: continue
            for d in range(D):
                for p in range(P):
                    m.add(cp_model.LinearExpr.Sum(
                        [v for a_i in t_grp for v in vA(a_i, d, p)]) <= 1)
                    m.add(cp_model.LinearExpr.Sum(
                        [v for a_i in t_grp for v in vB(a_i, d, p)]) <= 1)

        # Teacher availability (day + per-slot restrictions)
        for a_i in grp_set:
            mask    = _avail_mask(assignments[a_i].teacher, D)
            blocked = _blocked_slots(assignments[a_i].teacher, D, P)
            for d in range(D):
                for p in range(P):
                    if mask[d] != '1' or (d, p) in blocked:
                        m.add(xb_[a_i, d, p] == 0)
                        if alt_count[a_i]:
                            m.add(xa_[a_i, d, p] == 0)
                            m.add(xb2_[a_i, d, p] == 0)

        # Max lessons per day
        for t in teachers:
            t_grp = [a_i for a_i in teacher_assignments[t.pk] if a_i in grp_set]
            if not t_grp: continue
            for d in range(D):
                m.add(cp_model.LinearExpr.Sum([v for a_i in t_grp
                      for p in range(P) for v in vA(a_i, d, p)]) <= t.max_lessons_per_day)
                m.add(cp_model.LinearExpr.Sum([v for a_i in t_grp
                      for p in range(P) for v in vB(a_i, d, p)]) <= t.max_lessons_per_day)

        _matched_ct = matched_cross_keys or set()

        # Co-scheduled groups (same subject, different teachers → same slot)
        for (cls_pk, subj_pk), gmap in group_map.items():
            if (cls_pk, subj_pk) in _matched_ct:
                continue  # handled by cross-subject pairing below
            g_ais = list(gmap.values())
            if len(g_ais) < 2: continue
            if len({assignments[a_i].teacher_id for a_i in g_ais}) <= 1: continue
            rep = g_ais[0]
            for other in g_ais[1:]:
                for d in range(D):
                    for p in range(P):
                        m.add(xb_[rep, d, p] == xb_[other, d, p])
                        r_xa = xa_[rep, d, p]; o_xa = xa_[other, d, p]
                        r_xb2 = xb2_[rep, d, p]; o_xb2 = xb2_[other, d, p]
                        if not (isinstance(r_xa, int) and isinstance(o_xa, int)):
                            m.add(r_xa == o_xa)
                        if not (isinstance(r_xb2, int) and isinstance(o_xb2, int)):
                            m.add(r_xb2 == o_xb2)

        # Cross-subject pairing: teacher group g1 ↔ cross_g2, teacher group g2 ↔ cross_g1
        _cs_pairs = cross_subj_pairs or []
        for (s_g1_ais, s_g2_ais, c_g1, c_g2, mode, c_is_cross) in _cs_pairs:
            t_base = sum(base_count[ai] for ai in s_g1_ais)
            c_base = base_count[c_g1]
            _tag = f'{s_g1_ais[0]}_{c_g1}'
            for d in range(D):
                for p in range(P):
                    if len(s_g1_ais) == 1:
                        has_T_g1 = xb_[s_g1_ais[0], d, p]
                        has_T_g2 = xb_[s_g2_ais[0], d, p]
                    else:
                        has_T_g1 = m.new_bool_var(f'p1htg1_{_tag}_{d}_{p}')
                        m.add_max_equality(has_T_g1, [xb_[ai, d, p] for ai in s_g1_ais])
                        has_T_g2 = m.new_bool_var(f'p1htg2_{_tag}_{d}_{p}')
                        m.add_max_equality(has_T_g2, [xb_[ai, d, p] for ai in s_g2_ais])
                    if mode == 'equal':
                        m.add(has_T_g1 == xb_[c_g2, d, p])
                        m.add(has_T_g2 == xb_[c_g1, d, p])
                    elif t_base <= c_base:
                        m.add(has_T_g1 <= xb_[c_g2, d, p])
                        m.add(has_T_g2 <= xb_[c_g1, d, p])
                    else:
                        m.add(xb_[c_g2, d, p] <= has_T_g1)
                        m.add(xb_[c_g1, d, p] <= has_T_g2)
            # Partial: remaining cross-teacher slots must still be co-scheduled.
            # Only for c_is_cross=True (cross-teacher "c" side can have both g1 and g2
            # at the same slot). Not for same-same pairs — would be infeasible.
            if mode == 'partial' and c_is_cross and c_base > t_base:
                n_remaining = c_base - t_base
                both_p1: list = []
                for d in range(D):
                    for p in range(P):
                        both = m.new_bool_var(f'p1cs_rg_{_tag}_{d}_{p}')
                        m.add_min_equality(both, [xb_[c_g1, d, p], xb_[c_g2, d, p]])
                        both_p1.append(both)
                m.add(cp_model.LinearExpr.Sum(both_p1) >= n_remaining)

        anchor_obj: list = []

        if with_pairing:
            # Same cross-teacher pairing logic as Phase 2 constraint 6b.
            # For each teacher T: T_g1 at inner ≤ sum(non-T g2) at inner, and vice versa.
            for c in classes:
                same_t_g1_p1: dict = defaultdict(list)
                same_t_g2_p1: dict = defaultdict(list)

                for (cls_pk, subj_pk), gmap in group_map.items():
                    if cls_pk != c.pk: continue
                    g_nums = sorted(gmap.keys())
                    if len(g_nums) < 2: continue
                    a_g1 = gmap[g_nums[0]]
                    a_g2 = gmap[g_nums[1]]
                    t1 = assignments[a_g1].teacher_id
                    t2 = assignments[a_g2].teacher_id
                    if t1 != t2: continue
                    same_t_g1_p1[t1].append(a_g1)
                    same_t_g2_p1[t1].append(a_g2)

                t_ids = list(same_t_g1_p1.keys())
                if len(t_ids) < 2: continue

                all_g1_p1 = [a_i for ais in same_t_g1_p1.values() for a_i in ais]
                all_g2_p1 = [a_i for ais in same_t_g2_p1.values() for a_i in ais]

                for t_id in t_ids:
                    t_g1 = same_t_g1_p1[t_id]
                    t_g2 = same_t_g2_p1[t_id]
                    non_t_g2 = [a_i for a_i in all_g2_p1
                                 if assignments[a_i].teacher_id != t_id]
                    non_t_g1 = [a_i for a_i in all_g1_p1
                                 if assignments[a_i].teacher_id != t_id]

                    for d in range(D):
                        for p in range(1, P - 1):
                            t_g1_A = cp_model.LinearExpr.Sum(
                                [v for a_i in t_g1 for v in vA(a_i, d, p)])
                            nt_g2_A = cp_model.LinearExpr.Sum(
                                [v for a_i in non_t_g2 for v in vA(a_i, d, p)])
                            m.add(t_g1_A <= nt_g2_A)

                            t_g1_B = cp_model.LinearExpr.Sum(
                                [v for a_i in t_g1 for v in vB(a_i, d, p)])
                            nt_g2_B = cp_model.LinearExpr.Sum(
                                [v for a_i in non_t_g2 for v in vB(a_i, d, p)])
                            m.add(t_g1_B <= nt_g2_B)

                            t_g2_A = cp_model.LinearExpr.Sum(
                                [v for a_i in t_g2 for v in vA(a_i, d, p)])
                            nt_g1_A = cp_model.LinearExpr.Sum(
                                [v for a_i in non_t_g1 for v in vA(a_i, d, p)])
                            m.add(t_g2_A <= nt_g1_A)

                            t_g2_B = cp_model.LinearExpr.Sum(
                                [v for a_i in t_g2 for v in vB(a_i, d, p)])
                            nt_g1_B = cp_model.LinearExpr.Sum(
                                [v for a_i in non_t_g1 for v in vB(a_i, d, p)])
                            m.add(t_g2_B <= nt_g1_B)

                # Anchor reward: co-schedule cross-teacher pairs (mirrors Phase 2 obj_anchor).
                # Gives Phase 1 the same pairing preference as Phase 2, so hints steer
                # the solver toward optimal cross-teacher combinations (e.g. Прус+Зайцева
                # and Прус+Лойко rather than Зайцева+Лойко when Прус has more lessons).
                for ii in range(len(t_ids)):
                    for jj in range(ii + 1, len(t_ids)):
                        ta, tb = t_ids[ii], t_ids[jj]
                        for d in range(D):
                            for p in range(P):
                                for wk_label, vv in (('A', vA), ('B', vB)):
                                    for src_a, src_b, dir_tag in (
                                        (same_t_g1_p1[ta], same_t_g2_p1[tb], 'ab'),
                                        (same_t_g2_p1[ta], same_t_g1_p1[tb], 'ba'),
                                    ):
                                        va = [v for ai in src_a for v in vv(ai, d, p)]
                                        vb = [v for ai in src_b for v in vv(ai, d, p)]
                                        if va and vb:
                                            tag = (f'{dir_tag}_{c.pk}_{ta}_{tb}'
                                                   f'_{d}_{p}_{wk_label}')
                                            ha = m.new_bool_var(f'p1anc_a_{tag}')
                                            hb = m.new_bool_var(f'p1anc_b_{tag}')
                                            m.add_max_equality(ha, va)
                                            m.add_max_equality(hb, vb)
                                            both = m.new_bool_var(f'p1anc_{tag}')
                                            m.add_min_equality(both, [ha, hb])
                                            anchor_obj.append(both)

        # Inner-period symmetry: same-teacher g1 and g2 must have equal counts of
        # inner-period slots — a Phase 1 proxy for the actual pairing constraint in Phase 2.
        if with_pairing and P >= 3:
            for (cls_pk, subj_pk), gmap in group_map.items():
                g_nums = sorted(gmap.keys())
                if len(g_nums) < 2:
                    continue
                a_g1 = gmap[g_nums[0]]
                a_g2 = gmap[g_nums[1]]
                if assignments[a_g1].teacher_id != assignments[a_g2].teacher_id:
                    continue
                if a_g1 not in grp_set or a_g2 not in grp_set:
                    continue
                inner_g1 = sum(xb_[a_g1, d, p]
                               for d in range(D) for p in range(1, P - 1))
                inner_g2 = sum(xb_[a_g2, d, p]
                               for d in range(D) for p in range(1, P - 1))
                m.add(inner_g1 == inner_g2)

        # 8b (hard, Phase 1 only): unpaired same-teacher group lessons must be at
        # period 0 (first) or period P-1 (last) — no middle periods allowed.
        # This gives Phase 2 strong hints for edge placement.
        if with_edge and edge_set and P >= 3:
            for a_i in edge_set:
                for d in range(D):
                    for p in range(1, P - 1):
                        m.add(xb_[a_i, d, p] == 0)
                        if alt_count[a_i]:
                            m.add(xa_[a_i, d, p] == 0)
                            m.add(xb2_[a_i, d, p] == 0)

        # Objective: same-teacher g1+g2 on same day (same as Phase 2 obj_same_day).
        # Without this, Phase 1 finds any feasible placement → g1/g2 land on random
        # days → Phase 2 hints are wrong → 25s isn't enough to fix them.
        same_day_obj: list = []
        for (cls_pk, subj_pk), gmap in group_map.items():
            g_nums = sorted(gmap.keys())
            if len(g_nums) < 2:
                continue
            a_g1 = gmap[g_nums[0]]
            a_g2 = gmap[g_nums[1]]
            if assignments[a_g1].teacher_id != assignments[a_g2].teacher_id:
                continue  # cross-teacher → forced to same slot already
            for d in range(D):
                g1_vars = [xb_[a_g1, d, p] for p in range(P)]
                g2_vars = [xb_[a_g2, d, p] for p in range(P)]
                if alt_count[a_g1]:
                    g1_vars += [xa_[a_g1, d, p] for p in range(P)]
                if alt_count[a_g2]:
                    g2_vars += [xa_[a_g2, d, p] for p in range(P)]
                has_g1 = m.new_bool_var(f'p1_hg1_{a_g1}_{d}')
                has_g2 = m.new_bool_var(f'p1_hg2_{a_g2}_{d}')
                m.add_max_equality(has_g1, g1_vars)
                m.add_max_equality(has_g2, g2_vars)
                both = m.new_bool_var(f'p1_both_{a_g1}_{a_g2}_{d}')
                m.add_min_equality(both, [has_g1, has_g2])
                same_day_obj.append(both)

        all_p1_obj = same_day_obj + anchor_obj
        if all_p1_obj:
            m.maximize(cp_model.LinearExpr.Sum(all_p1_obj))

        return m, xb_, xa_, xb2_

    solver1 = cp_model.CpSolver()
    solver1.parameters.max_time_in_seconds = 20.0
    solver1.parameters.num_search_workers = 8
    solver1.parameters.log_search_progress = False

    for with_pairing, with_edge in ((True, True), (True, False), (False, True), (False, False)):
        m, xb_, xa_, xb2_ = _build(with_pairing, with_edge)
        status = solver1.solve(m)
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            break
    else:
        return {}

    fixed: dict = {}
    for a_i in grp_set:
        fixed[a_i] = {'base': [], 'xa': [], 'xb2': []}
        for d in range(D):
            for p in range(P):
                if solver1.value(xb_[a_i, d, p]):
                    fixed[a_i]['base'].append((d, p))
                if alt_count[a_i]:
                    if solver1.value(xa_[a_i, d, p]):
                        fixed[a_i]['xa'].append((d, p))
                    if solver1.value(xb2_[a_i, d, p]):
                        fixed[a_i]['xb2'].append((d, p))
    return fixed


def generate(schedule_id: int, optimize_teachers: bool = True) -> tuple:
    import time
    _t0 = time.time()
    def _log(msg): print(f'[GEN +{time.time()-_t0:.1f}s] {msg}', flush=True)

    schedule = Schedule.objects.get(pk=schedule_id)
    D = schedule.days_per_week
    P = schedule.lessons_per_day

    assignments = list(
        TeacherSubject.objects.select_related(
            'teacher', 'subject', 'school_class', 'school_class__home_room'
        ).all()
    )
    teachers = list(Teacher.objects.all())
    classes  = list(SchoolClass.objects.select_related('home_room').all())
    rooms    = list(Room.objects.select_related('subject').all())

    specialized_capacity: dict = defaultdict(int)
    for r in rooms:
        if r.subject_id is not None:
            specialized_capacity[r.subject_id] += r.max_simultaneous

    class_assignments   = {c.pk: [] for c in classes}
    teacher_assignments = {t.pk: [] for t in teachers}
    for a_i, a in enumerate(assignments):
        class_assignments[a.school_class_id].append(a_i)
        teacher_assignments[a.teacher_id].append(a_i)

    group_map: dict = defaultdict(dict)
    for a_i, a in enumerate(assignments):
        if a.group is not None:
            group_map[(a.school_class_id, a.subject_id)][a.group] = a_i

    # canonical[c.pk]: one representative assignment per (class, subject).
    # For grouped subjects: one rep regardless of same/different teacher.
    # Used for uniform-load and no-window constraints (student-perspective count).
    seen_subject_keys: set = set()
    canonical: dict = {c.pk: [] for c in classes}
    for a_i, a in enumerate(assignments):
        if a.group is None:
            canonical[a.school_class_id].append(a_i)
        else:
            key = (a.school_class_id, a.subject_id)
            if key not in seen_subject_keys:
                seen_subject_keys.add(key)
                canonical[a.school_class_id].append(a_i)

    base_count: dict = {}
    alt_count:  dict = {}
    for a_i, a in enumerate(assignments):
        h = float(a.hours_per_week)
        base_count[a_i] = int(h)
        alt_count[a_i]  = 1 if (h - int(h)) >= 0.5 else 0

    class_total_A = {
        c.pk: sum(base_count[a_i] + alt_count[a_i] for a_i in canonical[c.pk])
        for c in classes
    }
    # -------------------------------------------------------------------------
    model = cp_model.CpModel()

    # xb[a_i, d, p]  -- base lesson, both weeks
    # xa[a_i, d, p]  -- alt lesson, week A only  (0 int if no alt)
    # xb2[a_i, d, p] -- alt lesson, week B only  (0 int if no alt)
    xb:  dict = {}
    xa:  dict = {}
    xb2: dict = {}
    for a_i in range(len(assignments)):
        for d in range(D):
            for p in range(P):
                xb[a_i, d, p] = model.new_bool_var(f'xb{a_i}_{d}_{p}')
                if alt_count[a_i]:
                    xa[a_i, d, p]  = model.new_bool_var(f'xa{a_i}_{d}_{p}')
                    xb2[a_i, d, p] = model.new_bool_var(f'xb2{a_i}_{d}_{p}')
                else:
                    xa[a_i, d, p]  = 0
                    xb2[a_i, d, p] = 0

    # Return list of BoolVars for assignment a_i at slot (d,p) in week A / B.
    # Always returns at least [xb_var]; appends xa or xb2 when present.
    def vars_A(a_i, d, p):
        v = [xb[a_i, d, p]]
        if alt_count[a_i]:
            v.append(xa[a_i, d, p])
        return v

    def vars_B(a_i, d, p):
        v = [xb[a_i, d, p]]
        if alt_count[a_i]:
            v.append(xb2[a_i, d, p])
        return v

    # Scalar LinearExpr helpers for single-assignment occupancy checks.
    def occ_A(a_i, d, p):
        return cp_model.LinearExpr.Sum(vars_A(a_i, d, p))

    def occ_B(a_i, d, p):
        return cp_model.LinearExpr.Sum(vars_B(a_i, d, p))

    # 1. Lesson counts
    for a_i in range(len(assignments)):
        model.add(sum(xb[a_i, d, p] for d in range(D) for p in range(P)) == base_count[a_i])
        if alt_count[a_i]:
            # alt lesson goes to exactly one of (week A, week B) in exactly one slot
            model.add(sum(xa[a_i, d, p] + xb2[a_i, d, p]
                          for d in range(D) for p in range(P)) == 1)
            # xa and xb2 can't both be 1 in the same slot
            for d in range(D):
                for p in range(P):
                    model.add(xa[a_i, d, p] + xb2[a_i, d, p] <= 1)

    # 2. Class conflict (week A and week B separately)
    for c in classes:
        whole = [a_i for a_i in class_assignments[c.pk] if assignments[a_i].group is None]
        grp: dict = defaultdict(list)
        for a_i in class_assignments[c.pk]:
            if assignments[a_i].group is not None:
                grp[assignments[a_i].group].append(a_i)

        for d in range(D):
            for p in range(P):
                for vfn in (vars_A, vars_B):
                    if whole:
                        wvars = [v for a_i in whole for v in vfn(a_i, d, p)]
                        model.add(cp_model.LinearExpr.Sum(wvars) <= 1)
                    for g_ais in grp.values():
                        gvars = [v for a_i in g_ais for v in vfn(a_i, d, p)]
                        model.add(cp_model.LinearExpr.Sum(gvars) <= 1)
                    for wc in whole:
                        for g_ai in (ai for g_ais in grp.values() for ai in g_ais):
                            mixed = vfn(wc, d, p) + vfn(g_ai, d, p)
                            model.add(cp_model.LinearExpr.Sum(mixed) <= 1)

    # 3. Teacher <= 1 lesson per slot per week
    for t in teachers:
        st = teacher_assignments[t.pk]
        for d in range(D):
            for p in range(P):
                tvA = [v for a_i in st for v in vars_A(a_i, d, p)]
                tvB = [v for a_i in st for v in vars_B(a_i, d, p)]
                model.add(cp_model.LinearExpr.Sum(tvA) <= 1)
                model.add(cp_model.LinearExpr.Sum(tvB) <= 1)

    # 4. Teacher availability (day + per-slot restrictions)
    for a_i, a in enumerate(assignments):
        mask    = _avail_mask(a.teacher, D)
        blocked = _blocked_slots(a.teacher, D, P)
        for d in range(D):
            for p in range(P):
                if mask[d] != '1' or (d, p) in blocked:
                    model.add(xb[a_i, d, p] == 0)
                    if alt_count[a_i]:
                        model.add(xa[a_i, d, p] == 0)
                        model.add(xb2[a_i, d, p] == 0)

    # 5. max_lessons_per_day (both weeks)
    for t in teachers:
        st = teacher_assignments[t.pk]
        for d in range(D):
            tdA = [v for a_i in st for p in range(P) for v in vars_A(a_i, d, p)]
            tdB = [v for a_i in st for p in range(P) for v in vars_B(a_i, d, p)]
            model.add(cp_model.LinearExpr.Sum(tdA) <= t.max_lessons_per_day)
            model.add(cp_model.LinearExpr.Sum(tdB) <= t.max_lessons_per_day)

    # Find cross-subject pairs (must be before constraint 6 and Phase 1 calls)
    cs_pairs, matched_cross_keys, matched_same_keys = _find_cross_subj_pairs(
        group_map, assignments, base_count, alt_count
    )
    _log(f'cross_subj_pairs={len(cs_pairs)}: ' + ', '.join(
        f"{assignments[s_g1_ais[0]].school_class}/"
        f"{'+'.join(assignments[ai].subject.short_name or assignments[ai].subject.name for ai in s_g1_ais)}"
        f"\u2194{assignments[c_g1].subject.short_name or assignments[c_g1].subject.name}"
        f"({'eq' if mode == 'equal' else 'part'})"
        for s_g1_ais, s_g2_ais, c_g1, c_g2, mode, c_is_cross in cs_pairs
    ))

    # Pair alt-only (0.5h) non-group subjects within the same class.
    # Each pair shares the same slot: one in week A, the other in week B.
    alt_by_class: dict = defaultdict(list)
    for a_i, a in enumerate(assignments):
        if base_count[a_i] == 0 and alt_count[a_i] == 1 and a.group is None:
            alt_by_class[a.school_class_id].append(a_i)
    alt_pairs: list = []
    for cls_pk, ais in alt_by_class.items():
        ais_sorted = sorted(ais, key=lambda i: assignments[i].subject.name)
        for i in range(0, len(ais_sorted) - 1, 2):
            alt_pairs.append((ais_sorted[i], ais_sorted[i + 1]))
    if alt_pairs:
        _log(f'alt_pairs={len(alt_pairs)}: ' + ', '.join(
            f"{assignments[a_x].school_class}/"
            f"{assignments[a_x].subject.short_name or assignments[a_x].subject.name}"
            f"+{assignments[a_y].subject.short_name or assignments[a_y].subject.name}"
            for a_x, a_y in alt_pairs
        ))

    # Cross-class alt pairs: якщо вчитель має той самий 0.5h предмет у паралельних
    # класах (однаковий grade), перший клас за алфавітом → тиждень А, другий → тиждень Б,
    # і обидва на ОДИН і той самий слот (xa[A,d,p] == xb2[B,d,p]).
    # Це дає вчителю один слот для обох паралелей замість двох різних слотів.
    _alt_paired_set = {a_i for pair in alt_pairs for a_i in pair}
    _ccw_groups: dict = defaultdict(list)
    for a_i, a in enumerate(assignments):
        if (base_count[a_i] == 0 and alt_count[a_i] == 1
                and a.group is None and a_i not in _alt_paired_set):
            _ccw_groups[(a.teacher_id, a.subject_id, a.school_class.grade)].append(a_i)

    cross_class_alt_pairs: list = []  # (a_week_A, a_week_B) — same slot, opposite weeks
    for ais in _ccw_groups.values():
        if len(ais) < 2:
            continue
        ais_sorted = sorted(ais, key=lambda i: assignments[i].school_class.letter)
        for i in range(0, len(ais_sorted) - 1, 2):
            cross_class_alt_pairs.append((ais_sorted[i], ais_sorted[i + 1]))

    if cross_class_alt_pairs:
        _log('cross_class_alt_pairs=' + ', '.join(
            f"{assignments[a_x].school_class}(А)"
            f"+{assignments[a_y].school_class}(Б)/"
            f"{assignments[a_x].subject.short_name or assignments[a_x].subject.name}"
            for a_x, a_y in cross_class_alt_pairs
        ))

    # 6. Group co-scheduling (same slot for all groups, both xb and xa/xb2)
    for (cls_pk, subj_pk), gmap in group_map.items():
        if (cls_pk, subj_pk) in matched_cross_keys:
            continue  # handled by cross-subject pairing below
        group_ais = list(gmap.values())
        if len(group_ais) >= 2:
            teacher_ids = {assignments[a_i].teacher_id for a_i in group_ais}
            if len(teacher_ids) > 1:
                rep = group_ais[0]
                for other in group_ais[1:]:
                    for d in range(D):
                        for p in range(P):
                            model.add(xb[rep, d, p] == xb[other, d, p])
                            r_xa  = xa[rep, d, p];   o_xa  = xa[other, d, p]
                            r_xb2 = xb2[rep, d, p];  o_xb2 = xb2[other, d, p]
                            if not (isinstance(r_xa, int) and isinstance(o_xa, int)):
                                model.add(r_xa == o_xa)
                            if not (isinstance(r_xb2, int) and isinstance(o_xb2, int)):
                                model.add(r_xb2 == o_xb2)

    # Cross-subject pairing (Phase 2): teacher group g1 slots ↔ cross_g2, and vice versa.
    # s_g1_ais / s_g2_ais — all same-teacher assignments for g1 / g2 of the matched teacher.
    for (s_g1_ais, s_g2_ais, c_g1, c_g2, mode, c_is_cross) in cs_pairs:
        t_base = sum(base_count[ai] for ai in s_g1_ais)
        c_base = base_count[c_g1]

        # Tag for unique bool var names
        _tag = f'{s_g1_ais[0]}_{c_g1}'

        for d in range(D):
            for p in range(P):
                if len(s_g1_ais) == 1:
                    has_T_g1 = xb[s_g1_ais[0], d, p]
                    has_T_g2 = xb[s_g2_ais[0], d, p]
                else:
                    has_T_g1 = model.new_bool_var(f'htg1_{_tag}_{d}_{p}')
                    model.add_max_equality(has_T_g1, [xb[ai, d, p] for ai in s_g1_ais])
                    has_T_g2 = model.new_bool_var(f'htg2_{_tag}_{d}_{p}')
                    model.add_max_equality(has_T_g2, [xb[ai, d, p] for ai in s_g2_ais])

                if mode == 'equal':
                    model.add(has_T_g1 == xb[c_g2, d, p])
                    model.add(has_T_g2 == xb[c_g1, d, p])
                elif t_base <= c_base:
                    # T has fewer slots → T's presence ⊆ cross presence
                    model.add(has_T_g1 <= xb[c_g2, d, p])
                    model.add(has_T_g2 <= xb[c_g1, d, p])
                else:
                    # T has more slots → cross presence ⊆ T's presence
                    model.add(xb[c_g2, d, p] <= has_T_g1)
                    model.add(xb[c_g1, d, p] <= has_T_g2)

        # For partial mode where cross-teacher has MORE slots than T (c_base > t_base):
        # the remaining (c_base - t_base) cross-teacher slots must still be co-scheduled
        # between c_g1 and c_g2 (equivalent to regular same-slot pairing for those slots).
        # This does NOT apply to same-same pairs (c_is_cross=False) because the "c" teacher
        # is also same-teacher and never schedules g1/g2 at the same slot anyway.
        if mode == 'partial' and c_is_cross and c_base > t_base:
            n_remaining = c_base - t_base
            both_vars: list = []
            for d in range(D):
                for p in range(P):
                    both = model.new_bool_var(f'cs_rg_{_tag}_{d}_{p}')
                    model.add_min_equality(both, [xb[c_g1, d, p], xb[c_g2, d, p]])
                    both_vars.append(both)
            model.add(cp_model.LinearExpr.Sum(both_vars) >= n_remaining)

    # 6b. Subject-level pairing consistency (hard):
    # Cross-teacher g1↔g2 pairing must be symmetric per subject: slots where teacher T's
    # g1 is co-scheduled with a non-T g2 must equal slots where T's g2 is co-scheduled
    # with a non-T g1.  Prevents g1 of one subject being paired while g2 is not.

    # Pre-index group_map by class to avoid O(classes × group_map) scan.
    cls_group_map: dict = defaultdict(dict)
    for (cls_pk, subj_pk), gmap in group_map.items():
        cls_group_map[cls_pk][subj_pk] = gmap

    # Stores pair BoolVars from 6b for dynamic edge reward in tier 3.
    # all_pair_vars[a_i][(d, p)] = BoolVar that is 1 when a_i is co-scheduled
    # with a cross-teacher partner at slot (d, p).  Used instead of the static
    # `actually_unpaired` set so the edge reward adapts to Phase 2 pairing choices.
    all_pair_vars: dict = defaultdict(dict)

    for c in classes:
        t_subj_pairs: dict = defaultdict(list)  # t_id → [(a_g1, a_g2), ...]
        for subj_pk, gmap in cls_group_map[c.pk].items():
            g_nums = sorted(gmap.keys())
            if len(g_nums) < 2:
                continue
            a_g1, a_g2 = gmap[g_nums[0]], gmap[g_nums[1]]
            if assignments[a_g1].teacher_id == assignments[a_g2].teacher_id:
                t_subj_pairs[assignments[a_g1].teacher_id].append((a_g1, a_g2))

        for t_id, subj_pairs in t_subj_pairs.items():
            t_g1_nums = {assignments[a_g1].group for a_g1, _ in subj_pairs}
            t_g2_nums = {assignments[a_g2].group for _, a_g2 in subj_pairs}

            cross_g2_ais: list = []  # non-T g2 → partner when T teaches g1
            cross_g1_ais: list = []  # non-T g1 → partner when T teaches g2
            for a_i in class_assignments[c.pk]:
                a = assignments[a_i]
                if a.teacher_id == t_id or a.group is None:
                    continue
                if a.group in t_g2_nums:
                    cross_g2_ais.append(a_i)
                elif a.group in t_g1_nums:
                    cross_g1_ais.append(a_i)

            if not cross_g2_ais and not cross_g1_ais:
                continue

            # Per-slot cross-partner presence (shared across all subjects of this teacher)
            has_cg2: dict = {}
            has_cg1: dict = {}
            for d in range(D):
                for p in range(P):
                    if cross_g2_ais:
                        v = model.new_bool_var(f'hcg2_{c.pk}_{t_id}_{d}_{p}')
                        model.add_max_equality(v, [xb[a_j, d, p] for a_j in cross_g2_ais])
                        has_cg2[d, p] = v
                    if cross_g1_ais:
                        v = model.new_bool_var(f'hcg1_{c.pk}_{t_id}_{d}_{p}')
                        model.add_max_equality(v, [xb[a_j, d, p] for a_j in cross_g1_ais])
                        has_cg1[d, p] = v

            for a_g1, a_g2 in subj_pairs:
                pc_g1: list = []
                pc_g2: list = []
                for d in range(D):
                    for p in range(P):
                        cg2 = has_cg2.get((d, p))
                        if cg2 is not None:
                            pv = model.new_bool_var(f'pair_g1_{a_g1}_{d}_{p}')
                            model.add_min_equality(pv, [xb[a_g1, d, p], cg2])
                            pc_g1.append(pv)
                            all_pair_vars[a_g1][d, p] = pv
                        cg1 = has_cg1.get((d, p))
                        if cg1 is not None:
                            pv = model.new_bool_var(f'pair_g2_{a_g2}_{d}_{p}')
                            model.add_min_equality(pv, [xb[a_g2, d, p], cg1])
                            pc_g2.append(pv)
                            all_pair_vars[a_g2][d, p] = pv

                if pc_g1 or pc_g2:
                    model.add(
                        cp_model.LinearExpr.Sum(pc_g1) ==
                        cp_model.LinearExpr.Sum(pc_g2)
                    )

    # obj_anchor: reward co-scheduling of same-teacher groups from DIFFERENT teachers.
    # For each class with 2+ teachers that each have same-teacher g1+g2 subjects:
    # reward slot (d,p) where T_A has any g1 AND T_B has any g2, and vice versa.
    # This is the main driver for cross-subject cross-teacher pairing optimization.
    obj_anchor: list = []
    for c in classes:
        # Collect same-teacher group subjects per teacher in this class
        t_g1s: dict = defaultdict(list)  # teacher_id → [a_i for g1 assignments]
        t_g2s: dict = defaultdict(list)  # teacher_id → [a_i for g2 assignments]
        for (cls_pk, subj_pk), gmap in group_map.items():
            if cls_pk != c.pk:
                continue
            g_nums = sorted(gmap.keys())
            if len(g_nums) < 2:
                continue
            a_g1 = gmap[g_nums[0]]
            a_g2 = gmap[g_nums[1]]
            if assignments[a_g1].teacher_id != assignments[a_g2].teacher_id:
                continue  # cross-teacher → already co-scheduled (constraint 6)
            t_id = assignments[a_g1].teacher_id
            t_g1s[t_id].append(a_g1)
            t_g2s[t_id].append(a_g2)

        t_ids = list(t_g1s.keys())
        if len(t_ids) < 2:
            continue

        for i in range(len(t_ids)):
            for j in range(i + 1, len(t_ids)):
                ta, tb = t_ids[i], t_ids[j]
                for d in range(D):
                    for p in range(P):
                        for wk_label, week_vars in (('A', vars_A), ('B', vars_B)):
                            # T_A g1 at slot AND T_B g2 at slot → reward
                            v_ta_g1 = [v for a_i in t_g1s[ta] for v in week_vars(a_i, d, p)]
                            v_tb_g2 = [v for a_i in t_g2s[tb] for v in week_vars(a_i, d, p)]
                            if v_ta_g1 and v_tb_g2:
                                has_a = model.new_bool_var(f'anc_ag1_{c.pk}_{ta}_{tb}_{d}_{p}_{wk_label}')
                                has_b = model.new_bool_var(f'anc_bg2_{c.pk}_{ta}_{tb}_{d}_{p}_{wk_label}')
                                model.add_max_equality(has_a, v_ta_g1)
                                model.add_max_equality(has_b, v_tb_g2)
                                both = model.new_bool_var(f'anc_ab_{c.pk}_{ta}_{tb}_{d}_{p}_{wk_label}')
                                model.add_min_equality(both, [has_a, has_b])
                                obj_anchor.append(both)
                            # T_A g2 at slot AND T_B g1 at slot → reward
                            v_ta_g2 = [v for a_i in t_g2s[ta] for v in week_vars(a_i, d, p)]
                            v_tb_g1 = [v for a_i in t_g1s[tb] for v in week_vars(a_i, d, p)]
                            if v_ta_g2 and v_tb_g1:
                                has_c = model.new_bool_var(f'anc_ag2_{c.pk}_{ta}_{tb}_{d}_{p}_{wk_label}')
                                has_d_ = model.new_bool_var(f'anc_bg1_{c.pk}_{ta}_{tb}_{d}_{p}_{wk_label}')
                                model.add_max_equality(has_c, v_ta_g2)
                                model.add_max_equality(has_d_, v_tb_g1)
                                both2 = model.new_bool_var(f'anc_ba_{c.pk}_{ta}_{tb}_{d}_{p}_{wk_label}')
                                model.add_min_equality(both2, [has_c, has_d_])
                                obj_anchor.append(both2)

    # 6c. Same-day scheduling for same-teacher groups — SOFT preference via objective.
    #
    #  Hard equality (g1_day == g2_day) was INFEASIBLE when combined with anchor
    #  pairing and teacher load constraints.  Instead: reward the solver for placing
    #  g1 and g2 on the same days; let it skip same-day when there is no room.
    #
    #  obj_same_day accumulates one bool per (group_pair, day, week) that equals 1
    #  when both g1 AND g2 have a lesson on that day in that week.
    obj_same_day: list = []
    for (cls_pk, subj_pk), gmap in group_map.items():
        g_nums = sorted(gmap.keys())
        if len(g_nums) < 2:
            continue
        a_g1 = gmap[g_nums[0]]
        a_g2 = gmap[g_nums[1]]
        if assignments[a_g1].teacher_id != assignments[a_g2].teacher_id:
            continue  # different teachers → already same-slot (constraint 6)

        for d in range(D):
            g1_A = [v for p in range(P) for v in vars_A(a_g1, d, p)]
            g2_A = [v for p in range(P) for v in vars_A(a_g2, d, p)]
            has_g1_A = model.new_bool_var(f'hasG1A_{a_g1}_{d}')
            has_g2_A = model.new_bool_var(f'hasG2A_{a_g2}_{d}')
            model.add_max_equality(has_g1_A, g1_A)
            model.add_max_equality(has_g2_A, g2_A)
            both_A = model.new_bool_var(f'bothA_{a_g1}_{a_g2}_{d}')
            model.add_min_equality(both_A, [has_g1_A, has_g2_A])
            obj_same_day.append(both_A)

            g1_B = [v for p in range(P) for v in vars_B(a_g1, d, p)]
            g2_B = [v for p in range(P) for v in vars_B(a_g2, d, p)]
            has_g1_B = model.new_bool_var(f'hasG1B_{a_g1}_{d}')
            has_g2_B = model.new_bool_var(f'hasG2B_{a_g2}_{d}')
            model.add_max_equality(has_g1_B, g1_B)
            model.add_max_equality(has_g2_B, g2_B)
            both_B = model.new_bool_var(f'bothB_{a_g1}_{a_g2}_{d}')
            model.add_min_equality(both_B, [has_g1_B, has_g2_B])
            obj_same_day.append(both_B)

    # 7. Uniform class load per day.
    #    lo  = base-lessons-only // D  (alt lessons belong to exactly one week,
    #          so requiring them in *both* week-A and week-B lower bounds is
    #          infeasible when alt_count ≥ 3 — each alt xa+xb2=1, can't double-count)
    #    hi  = ceil((base + alt) / D)  (upper bound uses total since at most all
    #          alt lessons land in the same week)
    for c in classes:
        sc = canonical[c.pk]
        if not sc:
            continue
        base_total = sum(base_count[a_i] for a_i in sc)
        alt_total  = sum(alt_count[a_i]  for a_i in sc)
        grand_total = base_total + alt_total
        if grand_total == 0:
            continue
        lo = base_total // D
        hi = ceil(grand_total / D)
        for d in range(D):
            dA = ([xb[a_i, d, p] for a_i in sc for p in range(P)]
                  + [xa[a_i, d, p] for a_i in sc for p in range(P) if alt_count[a_i]])
            s = cp_model.LinearExpr.Sum(dA)
            model.add(s >= lo)
            model.add(s <= hi)
            dB = ([xb[a_i, d, p] for a_i in sc for p in range(P)]
                  + [xb2[a_i, d, p] for a_i in sc for p in range(P) if alt_count[a_i]])
            s = cp_model.LinearExpr.Sum(dB)
            model.add(s >= lo)
            model.add(s <= hi)

    # 8. No windows: lessons must be consecutive starting from period 0.
    #    Uses OR-based combined occupancy over ALL assignments for the class
    #    (including both g1 and g2 of same-teacher groups), so every lesson
    #    visible in the class schedule is forced into a contiguous block.
    #    Previously this caused INFEASIBLE when combined with hard constraints
    #    6b and 9; those are now soft, so OR-based is safe again.
    #
    # 8b. Unpaired group lessons (only one group has a teacher assigned) must be
    #    placed FIRST or LAST in the day.  A group lesson without a partner creates
    #    a free period for the other half of the class; putting it at the edge
    #    lets those students arrive late or leave early instead of sitting idle.
    #    Implementation: if an unpaired lesson lands at period p (1 ≤ p ≤ P-2),
    #    force slot[d, p+1] = 0 (no class lesson after it).  Constraint 8
    #    compactness then guarantees p is the last slot of the day.

    unpaired_all: set = set()
    for a_i, a in enumerate(assignments):
        if a.group is None:
            continue
        cls_ais = class_assignments[a.school_class_id]
        can_pair = any(
            assignments[a_j].group is not None
            and assignments[a_j].group != a.group
            and assignments[a_j].teacher_id != a.teacher_id
            for a_j in cls_ais
            if a_j != a_i
        )
        if not can_pair:
            unpaired_all.add(a_i)

    # Build slot_A/slot_B per class (no-window constraint 8).
    # Saved in cls_slot_A/B so 8b can be built AFTER Phase 1 (see below).
    cls_slot_A: dict = {}
    cls_slot_B: dict = {}
    for c in classes:
        all_ais = class_assignments[c.pk]
        if not all_ais:
            continue
        slot_A: dict = {}
        slot_B: dict = {}
        for d in range(D):
            for p in range(P):
                a_vars = [v for a_i in all_ais for v in vars_A(a_i, d, p)]
                b_vars = [v for a_i in all_ais for v in vars_B(a_i, d, p)]
                slot_A[d, p] = model.new_bool_var(f'occA_{c.pk}_{d}_{p}')
                slot_B[d, p] = model.new_bool_var(f'occB_{c.pk}_{d}_{p}')
                model.add_max_equality(slot_A[d, p], a_vars)
                model.add_max_equality(slot_B[d, p], b_vars)
        for d in range(D):
            for p in range(1, P):
                model.add(slot_A[d, p] <= slot_A[d, p - 1])
                model.add(slot_B[d, p] <= slot_B[d, p - 1])
        cls_slot_A[c.pk] = slot_A
        cls_slot_B[c.pk] = slot_B
        # 8b is built AFTER Phase 1 (below) so we know which slots are truly unpaired

    # 9. No same-day repeats — hard "at most 1/day" for non-double subjects.
    for a_i, a in enumerate(assignments):
        if a.subject.can_be_double:
            continue
        total_lessons = base_count[a_i] + alt_count[a_i]
        if total_lessons <= 1:
            continue  # only 1 lesson per week → doubling impossible
        for d in range(D):
            day_A = [v for p in range(P) for v in vars_A(a_i, d, p)]
            day_B = [v for p in range(P) for v in vars_B(a_i, d, p)]
            model.add(cp_model.LinearExpr.Sum(day_A) <= 1)
            model.add(cp_model.LinearExpr.Sum(day_B) <= 1)

    # 9b. Can-be-double subjects: at most 2 lessons per day.
    #     (Constraint 9 skips these, so without this cap 3+ lessons can land on one day.)
    for a_i, a in enumerate(assignments):
        if not a.subject.can_be_double:
            continue
        total_lessons = base_count[a_i] + alt_count[a_i]
        if total_lessons <= 2:
            continue
        for d in range(D):
            day_A = [v for p in range(P) for v in vars_A(a_i, d, p)]
            day_B = [v for p in range(P) for v in vars_B(a_i, d, p)]
            model.add(cp_model.LinearExpr.Sum(day_A) <= 2)
            model.add(cp_model.LinearExpr.Sum(day_B) <= 2)

    # 10. Specialized room capacity
    subj_assignments: dict = defaultdict(list)
    for a_i, a in enumerate(assignments):
        if a.subject_id in specialized_capacity:
            subj_assignments[a.subject_id].append(a_i)
    for subj_pk, ais in subj_assignments.items():
        subj_obj = assignments[ais[0]].subject
        cap = (specialized_capacity[subj_pk] if subj_obj.allow_shared_room
               else sum(1 for r in rooms if r.subject_id == subj_pk))
        for d in range(D):
            for p in range(P):
                model.add(sum(occ_A(a_i, d, p) for a_i in ais) <= cap)
                model.add(sum(occ_B(a_i, d, p) for a_i in ais) <= cap)

    # 11. Grade-incompatible classes can't share a specialized room
    for subj_pk, ais in subj_assignments.items():
        subj_obj = assignments[ais[0]].subject
        if not subj_obj.allow_shared_room:
            continue

        grade_ais: dict = defaultdict(list)
        for a_i in ais:
            grade_ais[assignments[a_i].school_class.grade].append(a_i)
        grades = sorted(grade_ais)
        if len(grades) < 2:
            continue

        has_A: dict = {}
        has_B: dict = {}
        for g in grades:
            g_ais = grade_ais[g]
            for d in range(D):
                for p in range(P):
                    vA = model.new_bool_var(f'hgA_{subj_pk}_{g}_{d}_{p}')
                    has_A[g, d, p] = vA
                    model.add(vA <= sum(occ_A(a_i, d, p) for a_i in g_ais))
                    for a_i in g_ais:
                        model.add(vA >= xb[a_i, d, p])
                        if alt_count[a_i]:
                            model.add(vA >= xa[a_i, d, p])

                    vB = model.new_bool_var(f'hgB_{subj_pk}_{g}_{d}_{p}')
                    has_B[g, d, p] = vB
                    model.add(vB <= sum(occ_B(a_i, d, p) for a_i in g_ais))
                    for a_i in g_ais:
                        model.add(vB >= xb[a_i, d, p])
                        if alt_count[a_i]:
                            model.add(vB >= xb2[a_i, d, p])

        for i, g1 in enumerate(grades):
            for g2 in grades[i + 1:]:
                if abs(g1 - g2) > subj_obj.max_grade_diff:
                    for d in range(D):
                        for p in range(P):
                            model.add(has_A[g1, d, p] + has_A[g2, d, p] <= 1)
                            model.add(has_B[g1, d, p] + has_B[g2, d, p] <= 1)

    # 12. Soft: reward same-grade co-scheduling for shared-room subjects (PE/gym).
    # Weight 800 (below PAIR_WEIGHT=1000) makes full pairing strongly preferred
    # without risking infeasibility from hard constraints.
    obj_same_grade_room: list = []
    for subj_pk, s_ais in subj_assignments.items():
        if not assignments[s_ais[0]].subject.allow_shared_room:
            continue
        sg_grade_ais: dict = defaultdict(list)
        for a_i in s_ais:
            sg_grade_ais[assignments[a_i].school_class.grade].append(a_i)
        for sg_ais in sg_grade_ais.values():
            for ai, aj in combinations(sg_ais, 2):
                if assignments[ai].teacher_id == assignments[aj].teacher_id:
                    continue
                for d in range(D):
                    for p in range(P):
                        both = model.new_bool_var(f'sgr_{ai}_{aj}_{d}_{p}')
                        model.add_min_equality(both, [xb[ai, d, p], xb[aj, d, p]])
                        obj_same_grade_room.append(both)

    # 13. Class daily balance: any two days differ by at most 1 occupied period.
    #     Uses cls_slot_A/B (built in constraint 8) which count true class occupancy —
    #     same-teacher group pairs share one slot, cross-teacher pairs use two.
    #     This prevents 4-lesson days next to 6-lesson days for the same class.
    for c in classes:
        slot_A = cls_slot_A.get(c.pk)
        slot_B = cls_slot_B.get(c.pk)
        if not slot_A:
            continue
        day_occ_A = [cp_model.LinearExpr.Sum([slot_A[d, p] for p in range(P)])
                     for d in range(D)]
        day_occ_B = [cp_model.LinearExpr.Sum([slot_B[d, p] for p in range(P)])
                     for d in range(D)]
        for d1 in range(D):
            for d2 in range(d1 + 1, D):
                model.add(day_occ_A[d1] - day_occ_A[d2] <= 1)
                model.add(day_occ_A[d2] - day_occ_A[d1] <= 1)
                model.add(day_occ_B[d1] - day_occ_B[d2] <= 1)
                model.add(day_occ_B[d2] - day_occ_B[d1] <= 1)

    # 14. Alt pairing: xa[a_x,d,p] == xb2[a_y,d,p] → same slot, opposite weeks.
    #     Covers both same-class pairs (different subjects) and cross-class pairs
    #     (same subject, parallel classes of the same teacher).
    for (a_x, a_y) in alt_pairs + cross_class_alt_pairs:
        for d in range(D):
            for p in range(P):
                model.add(xa[a_x, d, p] == xb2[a_y, d, p])

    # -------------------------------------------------------------------------
    # Phase 1: schedule group lessons with cross-teacher pairing, then hint Phase 2.
    _log(f'{len(assignments)} assignments, '
         f'obj_anchor={len(obj_anchor)} obj_same_day={len(obj_same_day)} '
         f'obj_same_grade_room={len(obj_same_grade_room)} '
         f'unpaired_structural={len(unpaired_all)}')

    # Phase 1a: edge constraints only for structurally-unpaired lessons.
    # Running with edge for unpaired_all (even if small) prevents the solver from
    # masking future-unpaired slots via opportunistic cross-subject pairing.
    _log('phase1a start')
    grp_hints1 = _solve_group_phase(
        assignments, group_map, class_assignments, teacher_assignments,
        base_count, alt_count, classes, teachers, D, P,
        unpaired_edge=unpaired_all,
        cross_subj_pairs=cs_pairs, matched_cross_keys=matched_cross_keys,
    )
    _log('phase1a done')

    # Detect actually_unpaired: group assignments where at least one slot in Phase 1a
    # has no cross-teacher partner (different group, different teacher) in the same class.
    actually_unpaired: set = set(unpaired_all)  # start with structural unpaired
    if P >= 3 and grp_hints1:
        grp_slots: dict = {}   # a_i -> set of (d,p) scheduled in Phase 1a
        for a_i, slots in grp_hints1.items():
            grp_slots[a_i] = set(slots['base'])

        # Reverse index: (cls_pk, d, p) → list of group a_i scheduled there
        slot_to_grp: dict = defaultdict(list)
        for a_i, dps in grp_slots.items():
            a = assignments[a_i]
            if a.group is not None:
                for dp in dps:
                    slot_to_grp[a.school_class_id, dp].append(a_i)

        for a_i, slots_i in grp_slots.items():
            if a_i in actually_unpaired:
                continue
            a = assignments[a_i]
            if a.group is None:
                continue
            cls_pk = a.school_class_id
            for dp in slots_i:
                paired = any(
                    assignments[a_j].group != a.group
                    and assignments[a_j].teacher_id != a.teacher_id
                    for a_j in slot_to_grp.get((cls_pk, dp), [])
                    if a_j != a_i
                )
                if not paired:
                    actually_unpaired.add(a_i)
                    break

    _log(f'actually_unpaired={len(actually_unpaired)} after phase1a')
    for a_i in sorted(actually_unpaired):
        a = assignments[a_i]
        _log(f'  unp #{a_i}: {a.school_class} | {a.subject} гр.{a.group} | {a.teacher}')

    # Phase 1b: with hard edge constraints for actually_unpaired → stronger hints for Phase 2.
    _log('phase1b start')
    grp_hints = _solve_group_phase(
        assignments, group_map, class_assignments, teacher_assignments,
        base_count, alt_count, classes, teachers, D, P,
        unpaired_edge=actually_unpaired,
        cross_subj_pairs=cs_pairs, matched_cross_keys=matched_cross_keys,
    )
    _log('phase1b done, applying hints')

    # Phase 1b hints help Phase 2 find a good pairing arrangement quickly.
    # Edge placement for unpaired lessons is done in post-processing (see below).
    for a_i, slots in grp_hints.items():
        for d, p in slots['base']:
            model.add_hint(xb[a_i, d, p], 1)
        if alt_count[a_i]:
            for d, p in slots['xa']:
                model.add_hint(xa[a_i, d, p], 1)
            for d, p in slots['xb2']:
                model.add_hint(xb2[a_i, d, p], 1)

    # Also hint all_pair_vars: directly tell Phase 2 which slots already have
    # cross-teacher pairing per Phase 1b.  Without these hints Phase 2 can start
    # from a wrong pairing (e.g. Зайцева+Лойко) even when the slot hints already
    # reflect the correct pairing (Зайцева+Прус, Лойко+Прус).
    if all_pair_vars and grp_hints:
        slot_grp_hints: dict = defaultdict(list)
        for a_i, slots in grp_hints.items():
            a = assignments[a_i]
            if a.group is None:
                continue
            for d, p in slots['base']:
                slot_grp_hints[a.school_class_id, d, p].append(a_i)

        for a_i, dp_vars in all_pair_vars.items():
            a = assignments[a_i]
            for (d, p), pv in dp_vars.items():
                partners = slot_grp_hints.get((a.school_class_id, d, p), [])
                is_paired = any(
                    assignments[a_j].group != a.group
                    and assignments[a_j].teacher_id != a.teacher_id
                    for a_j in partners
                    if a_j != a_i
                )
                model.add_hint(pv, int(is_paired))

    # -------------------------------------------------------------------------
    # Objective — three tiers (PAIR >> ROOM_PAIR >> CLUSTER/EDGE):
    #
    # 1. PAIR_WEIGHT (1000): pairing bonuses — never sacrifice a grouping bond.
    #    + obj_same_day: split groups from same teacher on same day
    #    + obj_anchor:   follower-slot aligned with anchor teacher
    #
    # 2. ROOM_PAIR_WEIGHT (500): same-grade PE/shared-room co-scheduling.
    #    Kept below PAIR_WEIGHT so group-lesson pairing takes priority.
    #    Phase 3 only enforces room capacity (constraint 7b); pairing is soft.
    #
    # 3. CLUSTER_WEIGHT (100): anti-clustering for unpaired lessons.
    #    When a class has unpaired lessons from multiple subjects (e.g. 4А:
    #    Укр.мова, ЯДС, Укр.літ — all taught by one teacher to both groups),
    #    reward NOT scheduling two different subject-groups on the same day.
    #    This spreads them so each day has at most one g1+g2 pair, leaving
    #    both edge slots free for that pair.
    #
    #    EDGE_WEIGHT (100): V-shaped edge reward for each unpaired lesson.
    #    Reward = (P-1 − 2·min(p, P-1−p)) — max at period 0 and P−1, zero
    #    at centre.  Naturally pushes g1 → period 0, g2 → period P−1.
    #    Must be large enough that the 2-point gap between adjacent periods
    #    outweighs any residual slack in FEASIBLE (non-OPTIMAL) solutions.
    PAIR_WEIGHT      = 1000
    ROOM_PAIR_WEIGHT = 500   # same-grade PE pairing; above 400 but won't block Phase 2
    CLUSTER_WEIGHT   = 100
    EDGE_WEIGHT      = 100

    all_obj_vars: list = (list(obj_same_day) + list(obj_anchor)
                          + list(obj_same_grade_room))
    all_obj_wts:  list = ([PAIR_WEIGHT]        * len(obj_same_day)
                          + [PAIR_WEIGHT]      * len(obj_anchor)
                          + [ROOM_PAIR_WEIGHT] * len(obj_same_grade_room))

    if actually_unpaired and P >= 3:
        # --- tier 2: anti-clustering ---
        cls_subj_unp: dict = defaultdict(lambda: defaultdict(list))
        for a_i in actually_unpaired:
            a = assignments[a_i]
            cls_subj_unp[a.school_class_id][a.subject_id].append(a_i)

        for cls_pk, subj_dict in cls_subj_unp.items():
            subj_keys = list(subj_dict.keys())
            if len(subj_keys) < 2:
                continue  # only one subject → no clustering possible

            # "subj_on_day[subj, d]" = 1 iff class cls_pk has any lesson of this
            # unpaired subject on day d.
            sod: dict = {}
            for subj_pk, ais in subj_dict.items():
                for d in range(D):
                    v = model.new_bool_var(f'sod_{cls_pk}_{subj_pk}_{d}')
                    model.add(v <= sum(xb[ai, d, p] for ai in ais for p in range(P)))
                    for ai in ais:
                        for p in range(P):
                            model.add(v >= xb[ai, d, p])
                    sod[subj_pk, d] = v

            # For every pair of subjects and every day: reward NOT being together.
            # not_tog = 1  iff  NOT (subj_i on day d  AND  subj_j on day d)
            # Constraints: not_tog >= 1 − gi
            #              not_tog >= 1 − gj
            #              not_tog + gi + gj <= 2
            for i in range(len(subj_keys)):
                for j in range(i + 1, len(subj_keys)):
                    sk_i, sk_j = subj_keys[i], subj_keys[j]
                    for d in range(D):
                        gi = sod[sk_i, d]
                        gj = sod[sk_j, d]
                        nt = model.new_bool_var(f'ntog_{cls_pk}_{i}_{j}_{d}')
                        model.add(nt >= 1 - gi)
                        model.add(nt >= 1 - gj)
                        model.add(nt + gi + gj <= 2)
                        all_obj_vars.append(nt)
                        all_obj_wts.append(CLUSTER_WEIGHT)

    if P >= 3:
        # --- tier 3: edge reward (dynamic) ---
        # Reward same-teacher group lessons that are ACTUALLY unpaired in Phase 2
        # AND placed at period 0 (first) or the real last occupied period of the day.
        # Uses pair BoolVars from constraint 6b (all_pair_vars) so the reward adapts
        # to Phase 2 pairing choices — not fixed to Phase 1a `actually_unpaired`.
        # This fixes the case where Phase 2 chooses a different subject to unpair
        # than Phase 1a predicted, leaving that subject at inner positions with no
        # edge incentive.
        seen_edge: set = set()
        for (cls_pk, subj_pk), gmap in group_map.items():
            g_nums = sorted(gmap.keys())
            if len(g_nums) < 2:
                continue
            a_g1, a_g2 = gmap[g_nums[0]], gmap[g_nums[1]]
            if assignments[a_g1].teacher_id != assignments[a_g2].teacher_id:
                continue  # cross-teacher: always jointly scheduled, no free periods
            slot_A = cls_slot_A.get(cls_pk)
            if slot_A is None:
                continue
            for a_i in (a_g1, a_g2):
                if a_i in seen_edge:
                    continue
                seen_edge.add(a_i)
                pair_dp = all_pair_vars.get(a_i, {})
                for d in range(D):
                    for p in range(P):
                        pair_v = pair_dp.get((d, p))
                        # Edge condition: period 0 (first) or actual last of day
                        if p == 0:
                            is_edge = xb[a_i, d, 0]
                        elif p < P - 1:
                            is_edge = model.new_bool_var(f'el_{a_i}_{d}_{p}')
                            model.add_min_equality(
                                is_edge, [xb[a_i, d, p], slot_A[d, p + 1].Not()])
                        else:
                            is_edge = xb[a_i, d, P - 1]
                        # Reward only when edge AND unpaired
                        if pair_v is not None:
                            unp_edge = model.new_bool_var(f'ue_{a_i}_{d}_{p}')
                            model.add_min_equality(unp_edge, [is_edge, pair_v.Not()])
                            all_obj_vars.append(unp_edge)
                        else:
                            # No cross-teacher partners exist → always unpaired
                            all_obj_vars.append(is_edge)
                        all_obj_wts.append(EDGE_WEIGHT)

    if all_obj_vars:
        model.maximize(cp_model.LinearExpr.WeightedSum(all_obj_vars, all_obj_wts))

    # -------------------------------------------------------------------------
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 90.0
    solver.parameters.num_search_workers = 8
    solver.parameters.log_search_progress = False

    _log('phase2 solve start')
    status = solver.solve(model)
    _log(f'phase2 done: {solver.status_name(status)} in {solver.wall_time:.1f}s')

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        try:
            issues = _diagnose(schedule, assignments, base_count, alt_count, canonical,
                               class_total_A, teachers, teacher_assignments, classes, rooms,
                               specialized_capacity, D, P)
        except Exception as e:
            issues = [f'(помилка діагностики: {e})']
        lines = [f'Розвязок не знайдено ({solver.status_name(status)})']
        if issues:
            lines.append('Можливі причини:')
            lines.extend(f'- {i}' for i in issues)
        else:
            lines.append('Базові перевірки пройшли -- причина у взаємодії обмежень.')
            lines.append(_summary(schedule, assignments, base_count, alt_count, canonical,
                                  class_total_A, teachers, teacher_assignments, classes, D, P))
        return False, '\n'.join(lines)

    # -------------------------------------------------------------------------
    # Debug: show where each unpaired lesson landed and whether it's at an edge.
    if actually_unpaired:
        # Build cls_periods: for each (cls_pk, day) → sorted list of scheduled periods
        cls_periods_dbg: dict = defaultdict(list)
        for a_i2, a2 in enumerate(assignments):
            for d2 in range(D):
                for p2 in range(P):
                    if solver.value(xb[a_i2, d2, p2]):
                        cls_periods_dbg[a2.school_class_id, d2].append(p2)
        for key in cls_periods_dbg:
            cls_periods_dbg[key].sort()

        day_names = ['Пн','Вт','Ср','Чт','Пт','Сб','Нд']
        for a_i in sorted(actually_unpaired):
            a = assignments[a_i]
            for d in range(D):
                for p in range(P):
                    if solver.value(xb[a_i, d, p]):
                        day_slots = cls_periods_dbg[a.school_class_id, d]
                        p_first = day_slots[0] if day_slots else -1
                        p_last  = day_slots[-1] if day_slots else -1
                        edge = '✓' if p in (p_first, p_last) else f'✗ (треба {p_first} або {p_last})'
                        _log(f'  unp #{a_i} {a.school_class} {a.subject} гр.{a.group} '
                             f'| {day_names[d]} ур.{p+1} {edge}')

    # -------------------------------------------------------------------------
    # Extract Phase 2 solution → run Phase 3 (gap optimization for non-group lessons)
    # -------------------------------------------------------------------------
    _log('extracting phase2 solution')
    final_vals: dict = {}
    for a_i2 in range(len(assignments)):
        base_s: list = []
        xa_s:   list = []
        xb2_s:  list = []
        for d2 in range(D):
            for p2 in range(P):
                if solver.value(xb[a_i2, d2, p2]):
                    base_s.append((d2, p2))
                if alt_count[a_i2]:
                    if solver.value(xa[a_i2, d2, p2]):
                        xa_s.append((d2, p2))
                    if solver.value(xb2[a_i2, d2, p2]):
                        xb2_s.append((d2, p2))
        final_vals[a_i2] = {'base': base_s, 'xa': xa_s, 'xb2': xb2_s}

    if optimize_teachers:
        _log('phase3 start (teacher-window optimization)')
        phase3_result = _solve_gap_phase(
            assignments, class_assignments, teacher_assignments,
            base_count, alt_count, classes, teachers, D, P, final_vals,
            alt_pairs=alt_pairs,
            specialized_capacity=specialized_capacity,
        )
        if phase3_result:
            final_vals.update(phase3_result)
            _log(f'phase3 done: non-group lessons re-optimized for {len(phase3_result)} assignments')
        else:
            _log('phase3 skipped or failed, using phase2 solution')
    else:
        _log('phase3 skipped (optimize_teachers=False)')

    # -------------------------------------------------------------------------
    # Room assignment
    # -------------------------------------------------------------------------
    room_usage_A: dict = {}   # (d,p) -> {room_pk: [grades]}
    room_usage_B: dict = {}
    home_room_ids = {c.home_room_id for c in classes if c.home_room_id}

    # Precompute room lookup structures to avoid O(rooms) scan per lesson
    rooms_by_subject: dict = defaultdict(list)
    general_rooms: list = []
    for r in rooms:
        if r.subject_id is not None:
            rooms_by_subject[r.subject_id].append(r)
        else:
            general_rooms.append(r)

    def _can_use(r, subject, grade, usage_dp):
        grades_here = list(dict.fromkeys(usage_dp.get(r.pk, [])))
        if len(grades_here) >= r.max_simultaneous:
            return False
        if grades_here:
            if not subject.allow_shared_room:
                return False
            if any(abs(grade - g) > subject.max_grade_diff for g in grades_here):
                return False
        return True

    def _mark(r, grade, usage_dp):
        usage_dp.setdefault(r.pk, []).append(grade)

    def find_room(subject, school_class, usage_dp):
        grade = school_class.grade
        specialized = rooms_by_subject.get(subject.pk, [])
        for r in specialized:
            if _can_use(r, subject, grade, usage_dp):
                return r
        if specialized:
            return None
        if school_class.home_room_id:
            hr = school_class.home_room
            if hr.subject_id is None and _can_use(hr, subject, grade, usage_dp):
                return hr
        for r in general_rooms:
            if r.pk not in home_room_ids and _can_use(r, subject, grade, usage_dp):
                return r
        return None

    shared_subj_ids = {
        pk for pk, ais in subj_assignments.items()
        if assignments[ais[0]].subject.allow_shared_room
    }

    # Collect scheduled slots
    base_sched  = []   # (d, p, grade_key, a_i)
    alt_a_sched = []   # week A only
    alt_b_sched = []   # week B only

    for a_i, a in enumerate(assignments):
        gk = a.school_class.grade if a.subject_id in shared_subj_ids else 0
        fv = final_vals[a_i]
        for d, p in fv['base']:
            base_sched.append((d, p, gk, a_i))
        for d, p in fv['xa']:
            alt_a_sched.append((d, p, gk, a_i))
        for d, p in fv['xb2']:
            alt_b_sched.append((d, p, gk, a_i))

    def _make_room_sort_key(sched):
        # Within each slot: process grades that appear 2+ times first so same-grade
        # pairs claim the shared room together.  Singles follow and get whatever
        # capacity remains (possibly adjacent-grade pairing).
        counts = Counter((d, p, gk) for d, p, gk, _ in sched if gk != 0)
        def key(t):
            d, p, gk, _ = t
            same_grade_count = counts.get((d, p, gk), 1) if gk != 0 else 1
            return (d * P + p, -same_grade_count, gk)
        return key

    base_sched.sort(key=_make_room_sort_key(base_sched))
    alt_a_sched.sort(key=_make_room_sort_key(alt_a_sched))
    alt_b_sched.sort(key=_make_room_sort_key(alt_b_sched))

    Lesson.objects.filter(schedule=schedule).delete()
    to_create = []

    # Base lessons (weeks 0 and 1, same slot)
    for d, p, _, a_i in base_sched:
        a = assignments[a_i]
        uA = room_usage_A.setdefault((d, p), {})
        uB = room_usage_B.setdefault((d, p), {})
        room = find_room(a.subject, a.school_class, uA)
        if room:
            _mark(room, a.school_class.grade, uA)
            _mark(room, a.school_class.grade, uB)
        for wk in (0, 1):
            to_create.append(Lesson(
                schedule=schedule, school_class=a.school_class,
                subject=a.subject, teacher=a.teacher, room=room,
                day=d, period=p, week=wk, group=a.group,
            ))

    # Alt-A lessons (week 0 only)
    for d, p, _, a_i in alt_a_sched:
        a = assignments[a_i]
        uA = room_usage_A.setdefault((d, p), {})
        room = find_room(a.subject, a.school_class, uA)
        if room:
            _mark(room, a.school_class.grade, uA)
        to_create.append(Lesson(
            schedule=schedule, school_class=a.school_class,
            subject=a.subject, teacher=a.teacher, room=room,
            day=d, period=p, week=0, group=a.group,
        ))

    # Alt-B lessons (week 1 only)
    for d, p, _, a_i in alt_b_sched:
        a = assignments[a_i]
        uB = room_usage_B.setdefault((d, p), {})
        room = find_room(a.subject, a.school_class, uB)
        if room:
            _mark(room, a.school_class.grade, uB)
        to_create.append(Lesson(
            schedule=schedule, school_class=a.school_class,
            subject=a.subject, teacher=a.teacher, room=room,
            day=d, period=p, week=1, group=a.group,
        ))

    Lesson.objects.bulk_create(to_create)
    n_base  = len(base_sched)
    n_alt_a = len(alt_a_sched)
    n_alt_b = len(alt_b_sched)
    total   = n_base * 2 + n_alt_a + n_alt_b
    return (True,
            f'Готово! {total} уроків '
            f'({n_base} базових x2 + {n_alt_a} черг.А + {n_alt_b} черг.Б) '
            f'за {solver.wall_time:.1f}с')
