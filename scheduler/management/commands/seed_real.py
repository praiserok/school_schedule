"""
Seed-команда для заповнення бази реальними даними зі школи.
Створює предмети, кабінети та класи нового навчального року
(паралелі зсунуті на 1 рік, 11 клас випустився).

Використання:
    python manage.py seed_real
    python manage.py seed_real --grade1 А Б        # новий 1А і 1Б
    python manage.py seed_real --grade1             # один 1кл без літери (default)
"""
from django.core.management.base import BaseCommand
from scheduler.models import Subject, Room, SchoolClass


SUBJECTS = [
    # (name, short_name, color, can_be_double, allow_shared_room, max_grade_diff)

    # ── НУШ, 1–4 кл ──────────────────────────────────────────────────────────
    ('Українська мова',                         'Укр. мова',  '#2471a3', False, False, 0),
    ('Математика',                              'Матем.',     '#1e8449', False, False, 0),
    ('Англійська мова',                         'Англ. мова', '#d35400', False, False, 0),
    ('ЯДС: мовно-читацька освіта',              'ЯДС МВО',   '#8e44ad', False, False, 0),
    ('ЯДС: природнича освіта',                  'ЯДС ПРО',   '#16a085', False, False, 0),
    ('ЯДС: громадянська та іст. освіта',        'ЯДС ГІО',   '#922b21', False, False, 0),
    ('ЯДС: соц. і здоровʼяз. освіта',           'ЯДС СЗО',   '#f39c12', False, False, 0),
    ('ЯДС: математична освіта',                 'ЯДС МАО',   '#1abc9c', False, False, 0),
    ('ЯДС: технологічна освіта',                'ЯДС ТЕО',   '#ca6f1e', False, False, 0),
    ('Фізична культура',    'Фізкульт.', '#3498db', True,  True,  1),   # спортзал, can_be_double
    ('Музичне мистецтво',               'Муз. мист.', '#e74c3c', False, False, 0),
    ('Образотворче мистецтво',          'Обр. мист.', '#f1c40f', False, False, 0),
    ('Інформатика',                     'Інформ.',    '#607d8b', False, False, 0),
    ('Технології',                      'Технол.',    '#795548', True,  False, 0),

    # ── Основна школа, 5–9 кл ────────────────────────────────────────────────
    ('Українська література',           'Укр. літ.',  '#5d6d7e', False, False, 0),
    ('Зарубіжна література',            'Заруб. літ.','#a04000', False, False, 0),
    ('Алгебра',                         'Алгебра',    '#1a5276', False, False, 0),
    ('Геометрія',                       'Геом.',      '#2e86c1', False, False, 0),
    ('Географія',                       'Геогр.',     '#117a65', False, False, 0),
    ('Біологія',                        'Біол.',      '#239b56', False, False, 0),
    ('Хімія',                           'Хімія',      '#76448a', False, False, 0),
    ('Фізика',                          'Фізика',     '#5dade2', False, False, 0),
    ('Всесвітня історія',               'Всесв. іст.','#c0392b', False, False, 0),
    ('Громадянська освіта',             'Гром. осв.', '#1d8348', False, False, 0),
    ('Пізнаємо природу',                'Пізн. прир.','#48c9b0', False, False, 0),
    ('Досліджуємо історію',             'Досл. іст.', '#f0b27a', False, False, 0),
    ('Трудове навчання',                'Труд. навч.','#dc7633', True,  False, 0),
    ('Мистецтво',                       'Мистецтво',  '#e91e63', False, False, 0),
    ("Основи здоровʼя",                 'Осн. здор.', '#28b463', False, False, 0),
    ('ЗБД',                             'ЗБД',        '#7b7d7d', False, False, 0),
    ('Родинні фінанси',                 'Род. фін.',  '#f4d03f', False, False, 0),
    ('Фінансово грамотний споживач',    'Фін. спож.', '#f39c12', False, False, 0),
    ('Економіка і фінанси',             'Екон. фін.', '#e59866', False, False, 0),

    # ── Старша школа, 10–11 кл ───────────────────────────────────────────────
    ('Прикладні фінанси',               'Прикл. фін.','#e67e22', False, False, 0),
    ('Фінансова культура',              'Фін. культ.','#e67e22', False, False, 0),
    ('Фінансова грамотність',           'Фін. грам.', '#f4d03f', False, False, 0),
    ('Основи правознавства',            'Осн. права', '#85929e', False, False, 0),
    ('Астрономія',                      'Астроном.',  '#1b2631', False, False, 0),
    ('Основи христ. етики',             'Христ. ет.', '#c8a882', False, False, 0),
]


# Кабінети: (назва, місткість, макс_одночасно, предмет_або_None)
# Спортзал: allow_shared_room у Subject, але кабінет прив'язується до предмету
ROOMS = [
    # Звичайні класні кімнати 1–18
    *[(str(i), 35, 1, None) for i in range(1, 19)],
    # Спеціалізовані
    ('Спортивний зал',  100, 2, 'Фізична культура'),   # 2 класи одночасно
    ('Актова зала',     150, 1, None),
    ('Майстерня',        30, 1, None),
    ('Бібліотека',       40, 1, None),
]


# Класи нового навчального року (зсув +1, 11-й випустився)
# (паралель, літера, домашній_кабінет_або_None)
# Домашній кабінет наслідується від минулого року:
#   новий 2кл (був 1кл) → кабінет 3
#   новий 3-А (була 2-А) → кабінет 9  тощо
CLASSES = [
    # Нові grade 1 — додаються через аргумент --grade1
    (2,  '',  '3'),    # був 1кл → кабінет 3
    (3,  'А', '9'),    # була 2-А → кабінет 9
    (3,  'Б', '14'),   # була 2-Б → кабінет 14
    (4,  'А', '16'),   # була 3-А → кабінет 16
    (4,  'Б', '7'),    # була 3-Б → кабінет 7
    (5,  '',  None),   # була 4кл (grade 5 вже без домашнього)
    (6,  'А', None),   # була 5-А
    (6,  'Б', None),   # була 5-Б
    (7,  'А', None),   # була 6-А
    (7,  'Б', None),   # була 6-Б
    (8,  'А', None),   # була 7-А
    (8,  'Б', None),   # була 7-Б
    (9,  '',  None),   # була 8кл
    (10, 'А', None),   # була 9-А
    (10, 'Б', None),   # була 9-Б
    (11, '',  None),   # була 10кл
    # 11кл (2025-26) випустилась — не включаємо
]


class Command(BaseCommand):
    help = 'Заповнює базу предметами, кабінетами та класами нового н.р.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--grade1', nargs='*', metavar='ЛІТЕРА',
            help='Літери нових 1-х класів. Без аргументів — один 1кл без літери. '
                 'Приклад: --grade1 А Б',
        )

    def handle(self, *args, **options):
        # ── Предмети ─────────────────────────────────────────────────────────
        created_s = 0
        for name, short, color, dbl, shared, diff in SUBJECTS:
            _, c = Subject.objects.get_or_create(
                name=name,
                defaults=dict(
                    short_name=short,
                    color=color,
                    can_be_double=dbl,
                    allow_shared_room=shared,
                    max_grade_diff=diff,
                ),
            )
            if c:
                created_s += 1
        self.stdout.write(f'Предмети: {created_s} створено, {len(SUBJECTS) - created_s} вже існувало')

        # ── Кабінети ─────────────────────────────────────────────────────────
        created_r = 0
        for name, cap, sim, subj_name in ROOMS:
            subj = Subject.objects.filter(name=subj_name).first() if subj_name else None
            _, c = Room.objects.get_or_create(
                name=name,
                defaults=dict(capacity=cap, max_simultaneous=sim, subject=subj),
            )
            if c:
                created_r += 1
        self.stdout.write(f'Кабінети: {created_r} створено, {len(ROOMS) - created_r} вже існувало')

        # ── Класи ─────────────────────────────────────────────────────────────
        created_c = 0

        # Нові перші класи
        grade1_letters = options.get('grade1')
        if not grade1_letters:
            # За замовчуванням (і при --grade1 без аргументів): один 1кл без літери
            grade1_letters = ['']
        for letter in grade1_letters:
            letter = letter.strip().upper() if letter else ''
            _, c = SchoolClass.objects.get_or_create(grade=1, letter=letter)
            if c:
                created_c += 1

        # Решта класів
        room_cache = {r.name: r for r in Room.objects.all()}
        for grade, letter, room_name in CLASSES:
            home = room_cache.get(room_name) if room_name else None
            _, c = SchoolClass.objects.get_or_create(
                grade=grade, letter=letter,
                defaults=dict(home_room=home),
            )
            if c:
                created_c += 1

        total_classes = len(CLASSES) + len(grade1_letters)
        self.stdout.write(f'Класи: {created_c} створено, {total_classes - created_c} вже існувало')
        self.stdout.write(self.style.SUCCESS('Готово! База заповнена.'))
