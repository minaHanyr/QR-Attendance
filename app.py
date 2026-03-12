from flask import Flask, render_template, request, redirect, url_for, send_file, jsonify, Response, session
import sqlite3
import qrcode
import os
import webbrowser
import urllib.parse
import pandas as pd
from datetime import datetime
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import cv2
from io import BytesIO

detector = cv2.QRCodeDetector()
app = Flask(__name__)


app.secret_key = "secret123"  # تأكد من وجود مفتاح سري

# إنشاء كائن قاعدة البيانات


# ─── DATABASE ───────────────────────────────────────────
def init_db():
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    # جدول المجموعات
    c.execute('''CREATE TABLE IF NOT EXISTS groups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE)''')
    c.execute('''CREATE TABLE IF NOT EXISTS students (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        parent_whatsapp TEXT,
        student_id TEXT NOT NULL UNIQUE,
        session_fee REAL DEFAULT 85,
        sessions_per_week INTEGER DEFAULT 1,
        group_id INTEGER,
        FOREIGN KEY (group_id) REFERENCES groups(id))''')
    # migrations لو الجدول موجود قديم
    try:
        c.execute('ALTER TABLE students ADD COLUMN session_fee REAL DEFAULT 85')
    except: pass
    try:
        c.execute('ALTER TABLE students ADD COLUMN group_id INTEGER')
    except: pass
    try:
        c.execute('ALTER TABLE students ADD COLUMN notes TEXT DEFAULT ""')
    except: pass
    try:
        c.execute('ALTER TABLE students ADD COLUMN sessions_per_week INTEGER DEFAULT 1')
    except: pass
    c.execute('''CREATE TABLE IF NOT EXISTS attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT NOT NULL, date TEXT NOT NULL, time TEXT NOT NULL,
        status TEXT DEFAULT 'حاضر',
        FOREIGN KEY (student_id) REFERENCES students(student_id))''')
    try:
        c.execute("ALTER TABLE attendance ADD COLUMN status TEXT DEFAULT 'حاضر'")
    except: pass
    # إعدادات وقت الحصة
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('session_start', '16:00')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('late_minutes', '15')")
    c.execute('''CREATE TABLE IF NOT EXISTS exams (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT, score INTEGER, date TEXT, max_score INTEGER NOT NULL,
        FOREIGN KEY (student_id) REFERENCES students(student_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE, password TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS homework (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT NOT NULL, date TEXT NOT NULL,
        score INTEGER NOT NULL, max_score INTEGER NOT NULL,
        FOREIGN KEY (student_id) REFERENCES students(student_id))''')
    # migration: إضافة عمود done للواجبات
    try:
        c.execute("ALTER TABLE homework ADD COLUMN done INTEGER DEFAULT 1")
    except: pass
    c.execute('''CREATE TABLE IF NOT EXISTS schedule (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day TEXT NOT NULL, time_from TEXT NOT NULL,
        time_to TEXT NOT NULL, subject TEXT NOT NULL, notes TEXT)''')
    # جدول الدفعات - كل سطر = دفعة واحدة
    c.execute('''CREATE TABLE IF NOT EXISTS fees (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT NOT NULL,
        amount REAL NOT NULL,
        description TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (student_id) REFERENCES students(student_id))''')
    # جدول الإجازات
    c.execute('''CREATE TABLE IF NOT EXISTS holidays (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL)''')
    # إعدادات تنبيه الغياب
    # جدول الإشعارات
    c.execute('''CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        link TEXT DEFAULT '',
        is_read INTEGER DEFAULT 0,
        created_at TEXT NOT NULL)''')
    c.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('absence_alert',  '3')")
    c.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('weekly_report_day','4')")
    c.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('lang','ar')")
    conn.commit()
    conn.close()

def create_default_users():
    users = [("admin","admin123"),("teacher1","1111"),("teacher2","2222"),("mina","2642004"),("tohmas","tommy")]
    conn = sqlite3.connect("attendance.db")
    c = conn.cursor()
    for u in users:
        c.execute("SELECT * FROM users WHERE username=?", (u[0],))
        if not c.fetchone():
            c.execute("INSERT INTO users (username,password) VALUES (?,?)", u)
    conn.commit(); conn.close()

def add_sample_students():
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM students')
    if c.fetchone()[0] == 0:
        for s in [("Ahmed","STD1001"),("Mona","STD1002"),("Youssef","STD1003")]:
            c.execute('INSERT INTO students (name, student_id) VALUES (?,?)', s)
    conn.commit(); conn.close()

def generate_qr_code(student_id, name=""):
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
    qr.add_data(student_id)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    os.makedirs('static/qr_codes', exist_ok=True)
    img.save(f"static/qr_codes/{student_id}.png")

def get_setting(key, default=''):
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = c.fetchone(); conn.close()
    return row[0] if row else default

def mark_attendance(student_id):
    """تسجيل حضور + تسجيل دفع + تحديد متأخر"""
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    date = datetime.now().strftime("%Y-%m-%d")
    time_now = datetime.now().strftime("%H:%M:%S")
    c.execute('SELECT * FROM attendance WHERE student_id=? AND date=?', (student_id, date))
    if c.fetchone() is None:
        # حساب متأخر ولا لأ
        status = 'حاضر'
        try:
            from datetime import timedelta
            # ميعاد الطالب الخاص أولاً، لو مش موجود يستخدم الـ global
            c.execute("SELECT session_time FROM students WHERE student_id=?", (student_id,))
            st_row = c.fetchone()
            student_session_time = (st_row[0] if st_row and st_row[0] else '').strip()
            session_start = student_session_time or get_setting('session_start', '00:00')
            late_minutes  = int(get_setting('late_minutes', '15'))
            if session_start and session_start != '00:00':
                start_dt = datetime.strptime(f"{date} {session_start}", "%Y-%m-%d %H:%M")
                deadline  = start_dt + timedelta(minutes=late_minutes)
                now_dt    = datetime.strptime(f"{date} {time_now[:5]}", "%Y-%m-%d %H:%M")
                if now_dt > deadline:
                    status = 'متأخر'
        except:
            pass

        c.execute('INSERT INTO attendance (student_id, date, time, status) VALUES (?,?,?,?)',
                  (student_id, date, time_now, status))
        c.execute("SELECT name, parent_whatsapp, session_fee FROM students WHERE student_id=?", (student_id,))
        student = c.fetchone()
        if student:
            name, phone, fee = student[0], student[1], student[2] or 85
            c.execute('INSERT INTO fees (student_id, amount, description, created_at) VALUES (?,?,?,?)',
                      (student_id, fee, f'حصة {date}', date))

            # إشعار داخلي
            icon = '⏰' if status == 'متأخر' else '✅'
            c.execute("INSERT INTO notifications (type,title,body,link,created_at) VALUES (?,?,?,?,?)",
                      ('attendance', f'{icon} {name}',
                       f'سجّل الحضور الساعة {time_now[:5]} — {status}',
                       f'/student_history/{student_id}',
                       datetime.now().strftime("%Y-%m-%d %H:%M")))
        conn.commit()
        conn.close()
        return True, status
    conn.close()
    return False, None


# ─── LOGIN ──────────────────────────────────────────────
@app.route('/login', methods=['GET','POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = sqlite3.connect('attendance.db')
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE username=? AND password=?", (username, password))
        user = c.fetchone(); conn.close()
        if user:
            session['users'] = username
            return redirect(url_for('dashboard'))
        error = '❌ اسم المستخدم أو الباسورد غلط!'
    return render_template("login.html", error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
def home():
    return redirect(url_for('login'))

# ─── DASHBOARD ──────────────────────────────────────────
@app.route('/dashboard_data')
def dashboard_data():
    """API للتحديث التلقائي بدون refresh"""
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM students'); total = c.fetchone()[0]
    c.execute('SELECT COUNT(DISTINCT student_id) FROM attendance WHERE date=date("now")'); present = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM attendance WHERE date=date('now') AND status='متأخر'"); late = c.fetchone()[0]
    # آخر 5 حاضرين اليوم
    c.execute('''SELECT s.name, a.time, a.status FROM attendance a
        JOIN students s ON s.student_id=a.student_id
        WHERE a.date=date("now") ORDER BY a.time DESC LIMIT 5''')
    recent = [{'name':r[0],'time':r[1],'status':r[2]} for r in c.fetchall()]
    conn.close()
    return jsonify({
        'present': present, 'absent': total - present,
        'late': late, 'total': total,
        'recent': recent,
        'time': datetime.now().strftime("%H:%M:%S")
    })

@app.route('/dashboard')
def dashboard():
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    # أرقام أساسية
    c.execute('SELECT COUNT(*) FROM students'); total_students = c.fetchone()[0]
    c.execute('SELECT COUNT(DISTINCT student_id) FROM attendance WHERE date=date("now")'); present_today = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM attendance WHERE date=date('now') AND status='متأخر'"); late_today = c.fetchone()[0]
    c.execute('SELECT COALESCE(SUM(amount),0) FROM fees'); total_collected = c.fetchone()[0]
    # آخر 7 أيام حضور
    c.execute('''SELECT date, COUNT(DISTINCT student_id) FROM attendance
        WHERE date >= date("now","-6 days") GROUP BY date ORDER BY date''')
    att_rows = c.fetchall()
    # توزيع الحضور/الغياب/التأخر اليوم
    c.execute("SELECT COUNT(*) FROM attendance WHERE date=date('now') AND status='حاضر'"); on_time = c.fetchone()[0]
    # أكتر 5 طلاب حضوراً
    c.execute('''SELECT s.name, COUNT(a.id) as cnt FROM attendance a
        JOIN students s ON s.student_id=a.student_id
        GROUP BY a.student_id ORDER BY cnt DESC LIMIT 5''')
    top_students = c.fetchall()
    conn.close()
    import json
    chart_dates  = json.dumps([r[0] for r in att_rows])
    chart_counts = json.dumps([r[1] for r in att_rows])
    return render_template('dashboard.html',
        total_students=total_students,
        present_today=present_today,
        absent_today=total_students - present_today,
        late_today=late_today,
        on_time=on_time,
        total_collected=int(total_collected),
        chart_dates=chart_dates,
        chart_counts=chart_counts,
        top_students=top_students)

# ─── STUDENTS ───────────────────────────────────────────
@app.route('/students')
def students():
    search   = request.args.get('search', '')
    group_id = request.args.get('group_id', '')
    sort     = request.args.get('sort', 'name')
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    query = '''SELECT s.id, s.name, s.student_id, s.session_fee,
        COALESCE(g.name,'—') as group_name,
        COUNT(DISTINCT a.id) as sessions,
        COALESCE(SUM(f.amount),0) as paid,
        s.notes
        FROM students s
        LEFT JOIN groups g ON g.id = s.group_id
        LEFT JOIN attendance a ON a.student_id = s.student_id
        LEFT JOIN fees f ON f.student_id = s.student_id
        WHERE 1=1'''
    params = []
    if search:
        query += " AND (s.name LIKE ? OR s.student_id LIKE ?)"
        params += ['%'+search+'%', '%'+search+'%']
    if group_id:
        query += " AND s.group_id = ?"
        params.append(group_id)
    sort_map = {'name':'s.name','sessions':'sessions DESC','paid':'paid DESC'}
    query += f" GROUP BY s.id ORDER BY {sort_map.get(sort,'s.name')}"
    c.execute(query, params)
    students_list = c.fetchall()
    c.execute("SELECT id, name FROM groups ORDER BY name")
    groups = c.fetchall()
    conn.close()
    return render_template('students.html', students=students_list,
                           groups=groups, search=search,
                           selected_group=group_id, sort=sort)

@app.route('/students_json')
def students_json():
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute('SELECT name, student_id FROM students ORDER BY name')
    data = [{'name': r[0], 'student_id': r[1]} for r in c.fetchall()]
    conn.close()
    return jsonify(data)

@app.route('/add_student', methods=['GET','POST'])
def add_student():
    if request.method == 'POST':
        name = request.form['name']
        student_id = request.form['student_id']
        parent_whatsapp = request.form.get('parent_whatsapp','')
        session_fee = float(request.form.get('session_fee', 85) or 85)
        sessions_per_week = int(request.form.get('sessions_per_week', 1) or 1)
        group_id = request.form.get('group_id') or None
        notes = request.form.get('notes', '')
        conn = sqlite3.connect('attendance.db')
        c = conn.cursor()
        try:
            c.execute('INSERT INTO students (name, student_id, parent_whatsapp, session_fee, sessions_per_week, group_id, notes) VALUES (?,?,?,?,?,?,?)',
                (name, student_id, parent_whatsapp, session_fee, sessions_per_week, group_id, notes))
            conn.commit()
            generate_qr_code(student_id, name)
        except Exception as e:
            conn.close(); return f"خطأ: {e}", 400
        conn.close()
        return redirect(url_for('students'))
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute('SELECT id, name FROM groups ORDER BY name')
    groups = c.fetchall(); conn.close()
    return render_template('add_student.html', groups=groups)

@app.route('/delete_student/<id>')
def delete_student(id):
    conn = sqlite3.connect('attendance.db')
    conn.cursor().execute("DELETE FROM students WHERE id=?", (id,))
    conn.commit(); conn.close()
    return redirect(url_for('students'))

@app.route('/edit_student/<id>', methods=['GET','POST'])
def edit_student(id):
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    if request.method == 'POST':
        name = request.form['name']
        session_fee = float(request.form.get('session_fee', 85) or 85)
        sessions_per_week = int(request.form.get('sessions_per_week', 1) or 1)
        group_id = request.form.get('group_id') or None
        notes = request.form.get('notes', '')
        c.execute("UPDATE students SET name=?, session_fee=?, sessions_per_week=?, group_id=?, notes=? WHERE id=?",
                  (name, session_fee, sessions_per_week, group_id, notes, id))
        conn.commit(); conn.close()
        return redirect(url_for('students'))
    c.execute("SELECT id, name, session_fee, sessions_per_week, group_id, notes FROM students WHERE id=?", (id,))
    student = c.fetchone()
    c.execute('SELECT id, name FROM groups ORDER BY name')
    groups = c.fetchall(); conn.close()
    return render_template("edit_student.html", student=student, groups=groups)

# ─── GROUPS ─────────────────────────────────────────────
@app.route('/groups', methods=['GET','POST'])
def groups():
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            name = request.form.get('name','').strip()
            if name:
                try:
                    c.execute('INSERT INTO groups (name) VALUES (?)', (name,))
                    conn.commit()
                except: pass
        elif action == 'delete':
            c.execute('UPDATE students SET group_id=NULL WHERE group_id=?', (request.form['id'],))
            c.execute('DELETE FROM groups WHERE id=?', (request.form['id'],))
            conn.commit()
        elif action == 'rename':
            c.execute('UPDATE groups SET name=? WHERE id=?',
                      (request.form['name'], request.form['id']))
            conn.commit()
    c.execute('''SELECT g.id, g.name, COUNT(s.id) as cnt
        FROM groups g LEFT JOIN students s ON s.group_id=g.id
        GROUP BY g.id ORDER BY g.name''')
    groups_list = c.fetchall()
    conn.close()
    return render_template('groups.html', groups=groups_list)

@app.route('/delete_attendance/<int:id>')
def delete_attendance(id):
    conn = sqlite3.connect('attendance.db')
    conn.cursor().execute("DELETE FROM attendance WHERE id=?", (id,))
    conn.commit(); conn.close()
    return redirect(url_for('report'))

@app.route('/notify_absent', methods=['POST'])
def notify_absent():
    """إرسال واتساب للطلاب الغايبين اليوم"""
    date = request.form.get('date', datetime.now().strftime("%Y-%m-%d"))
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    # الطلاب الغايبين = كل الطلاب - الحاضرين
    c.execute('''SELECT s.name, s.parent_whatsapp FROM students s
        WHERE s.parent_whatsapp IS NOT NULL AND s.parent_whatsapp != ""
        AND s.student_id NOT IN (
            SELECT student_id FROM attendance WHERE date=?
        )''', (date,))
    absent = c.fetchall()
    conn.close()
    count = 0
    for name, phone in absent:
        msg = (
        f" تنبيه غياب — \n"
        f"──────────────────\n"
        f"الطالب: {name}\n"
        f"غائب اليوم ولم يحضر الحصة\n"
        f"──────────────────\n"
        f"*التاريخ:* {date} \n\n"
        f"──────────────────\n"
        f"نرجو المتابعة والاهتمام بحضور ابنكم "
    )
        encoded_msg = msg.replace(' ', '%20').replace('\n', '%0A')
        webbrowser.open(f"https://wa.me/{phone}?text={encoded_msg}")
        count += 1
    return jsonify({'success': True, 'count': count, 'message': f'تم فتح واتساب لـ {count} غائب'})

# ─── SCAN ───────────────────────────────────────────────
@app.route('/scan')
def scan():
    return render_template('scan.html')

@app.route('/scan_qr', methods=['POST'])
def scan_qr():
    data = request.get_json()
    student_id = (data.get('student_id') or '').strip()
    if not student_id:
        return jsonify({'success': False, 'message': 'QR غير صالح', 'student_name': ''})

    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute('SELECT name, session_fee, notes FROM students WHERE student_id=?', (student_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'message': 'الطالب غير موجود', 'student_name': student_id})

    student_name = row[0]
    session_fee = row[1] or 85
    student_notes = row[2] or ''

    # إجمالي الدفعات
    c.execute('SELECT COALESCE(SUM(amount),0) FROM fees WHERE student_id=?', (student_id,))
    total_paid = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM attendance WHERE student_id=?', (student_id,))
    prev_sessions = c.fetchone()[0]
    # واجب اليوم
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("SELECT done FROM homework WHERE student_id=? AND date=?", (student_id, today))
    hw_row = c.fetchone()
    homework_today = hw_row[0] if hw_row is not None else None  # None = لسه مسجلش
    conn.close()

    # تسجيل الحضور + الدفع أوتوماتيك
    success, att_status = mark_attendance(student_id)

    if success:
        new_sessions = prev_sessions + 1
        new_paid = total_paid + session_fee
        late_note = '  متأخر!' if att_status == 'متأخر' else ''
        msg = f' تم تسجيل حضور {student_name} + دفع {session_fee} ج{late_note}'
    else:
        new_sessions = prev_sessions
        new_paid = total_paid
        att_status = None
        msg = f'ℹ️ {student_name} سجّل حضوره اليوم مسبقاً'

    return jsonify({
        'success': success,
        'message': msg,
        'student_name': student_name,
        'notes': student_notes,
        'att_status': att_status,
        'homework_today': homework_today,
        'fees': {
            'session_fee': session_fee,
            'total_sessions': new_sessions,
            'total_paid': new_paid,
        }
    })

@app.route('/student_info/<student_id>')
def student_info(student_id):
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute('SELECT name, student_id, parent_whatsapp FROM students WHERE student_id=?', (student_id,))
    row = c.fetchone(); conn.close()
    if row:
        return jsonify({'name': row[0], 'student_id': row[1], 'parent_phone': row[2]})
    return jsonify({'name': None}), 404

# ─── HOMEWORK / EXAM ────────────────────────────────────
@app.route('/api/homework', methods=['POST'])
def api_homework():
    """تسجيل الواجب — done=1 عمله، done=0 ما عملوش"""
    data = request.get_json()
    student_id = data.get('student_id')
    done       = int(data.get('done', 1))  # 1=عمل, 0=معمل
    if not student_id:
        return jsonify({'success': False, 'message': 'بيانات ناقصة'})
    date = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    # نتجنب التكرار في نفس اليوم
    c.execute("SELECT id FROM homework WHERE student_id=? AND date=?", (student_id, date))
    if c.fetchone():
        c.execute("UPDATE homework SET done=?, score=?, max_score=1 WHERE student_id=? AND date=?",
                  (done, done, student_id, date))
    else:
        c.execute("INSERT INTO homework (student_id, date, score, max_score, done) VALUES (?,?,?,?,?)",
                  (student_id, date, done, 1, done))
    # إشعار داخلي
    c.execute("SELECT name FROM students WHERE student_id=?", (student_id,))
    row = c.fetchone()
    name = row[0] if row else student_id
    icon = '📝' if done else '❌'
    c.execute("INSERT INTO notifications (type,title,body,link,created_at) VALUES (?,?,?,?,?)",
              ('homework', f'{icon} {name}',
               'عمل الواجب ✅' if done else 'ما عملش الواجب ❌',
               f'/student_history/{student_id}',
               datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit(); conn.close()
    return jsonify({'success': True, 'message': '✅ تم التسجيل', 'done': done})

@app.route('/api/exam', methods=['POST'])
def api_exam():
    data = request.get_json()
    student_id, score, max_score = data.get('student_id'), data.get('score'), data.get('max_score', 1)
    if not student_id or score is None:
        return jsonify({'success': False, 'message': 'بيانات ناقصة'})
    conn = sqlite3.connect('attendance.db')
    conn.cursor().execute('INSERT INTO exams (student_id, date, score, max_score) VALUES (?,?,?,?)',
        (student_id, datetime.now().strftime("%Y-%m-%d"), score, max_score))
    conn.commit(); conn.close()
    return jsonify({'success': True, 'message': '✅ تم تسجيل الامتحان'})

@app.route('/mark_homework', methods=['GET','POST'])
def mark_homework():
    if request.method == 'POST':
        conn = sqlite3.connect('attendance.db')
        conn.cursor().execute('INSERT INTO homework (student_id, date, score, max_score) VALUES (?,?,?,?)',
            (request.form['student_id'], datetime.now().strftime("%Y-%m-%d"),
             request.form['score'], request.form['max_score']))
        conn.commit(); conn.close()
        ref = request.referrer or ''
        return redirect(ref if 'student_history' in ref else url_for('students'))
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor(); c.execute('SELECT id, name, student_id FROM students')
    students_list = c.fetchall(); conn.close()
    return render_template('mark_homework.html', students=students_list)

@app.route('/mark_exam', methods=['GET','POST'])
def mark_exam():
    if request.method == 'POST':
        conn = sqlite3.connect('attendance.db')
        conn.cursor().execute('INSERT INTO exams (student_id, date, score, max_score) VALUES (?,?,?,?)',
            (request.form['student_id'], datetime.now().strftime("%Y-%m-%d"),
             request.form['score'], request.form['max_score']))
        conn.commit(); conn.close()
        ref = request.referrer or ''
        return redirect(ref if 'student_history' in ref else url_for('students'))
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor(); c.execute('SELECT id, name, student_id FROM students')
    students_list = c.fetchall(); conn.close()
    return render_template('mark_exam.html', students=students_list)

# ─── STUDENT HISTORY / PERFORMANCE ─────────────────────
@app.route('/student_history/<student_id>')
def student_history(student_id):
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute('''SELECT s.id, s.name, s.student_id, s.parent_whatsapp,
        s.session_fee, s.notes, COALESCE(g.name,'—') as grp,
        s.sessions_per_week
        FROM students s LEFT JOIN groups g ON g.id=s.group_id
        WHERE s.student_id=?''', (student_id,))
    student_info = c.fetchone()
    c.execute('SELECT date, time, status FROM attendance WHERE student_id=? ORDER BY date DESC', (student_id,))
    attendance_history = c.fetchall()
    c.execute('SELECT date, done FROM homework WHERE student_id=? ORDER BY date DESC', (student_id,))
    homework_history = c.fetchall()
    c.execute('SELECT date, score, max_score FROM exams WHERE student_id=? ORDER BY date DESC', (student_id,))
    exam_history = c.fetchall()
    c.execute('SELECT COALESCE(SUM(amount),0) FROM fees WHERE student_id=?', (student_id,))
    total_paid = int(c.fetchone()[0])
    # إحصاءات
    total_att  = len(attendance_history)
    late_count = sum(1 for a in attendance_history if a[2] == 'متأخر')
    hw_done    = sum(1 for h in homework_history if h[1])
    hw_total   = len(homework_history)
    ex_avg     = round(sum(r[1]/r[2]*100 for r in exam_history)/len(exam_history)) if exam_history else 0
    conn.close()
    return render_template('student_history.html',
        student_info=student_info,
        attendance_history=attendance_history,
        homework_history=homework_history,
        exam_history=exam_history,
        total_paid=total_paid,
        total_att=total_att,
        late_count=late_count,
        hw_done=hw_done, hw_total=hw_total,
        ex_avg=ex_avg)

@app.route('/performance/<student_id>')
def performance(student_id):
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute('SELECT id, name, student_id, parent_whatsapp FROM students WHERE student_id=?', (student_id,))
    student_info = c.fetchone()
    c.execute('SELECT COUNT(*) FROM attendance WHERE student_id=?', (student_id,))
    total_attendance = c.fetchone()[0]
    c.execute('SELECT date, score, max_score FROM homework WHERE student_id=? ORDER BY date', (student_id,))
    homework_data = c.fetchall()
    c.execute('SELECT date, score, max_score FROM exams WHERE student_id=? ORDER BY date', (student_id,))
    exam_data = c.fetchall()
    conn.close()
    hw_avg = round(sum(r[1]/r[2]*100 for r in homework_data)/len(homework_data), 1) if homework_data else 0
    ex_avg = round(sum(r[1]/r[2]*100 for r in exam_data)/len(exam_data), 1) if exam_data else 0
    return render_template('performance.html', student_info=student_info,
        total_attendance=total_attendance, homework_data=homework_data,
        exam_data=exam_data, hw_avg=hw_avg, ex_avg=ex_avg)

@app.route('/performance_chart/<student_id>/<chart_type>')
def performance_chart(student_id, chart_type):
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    if chart_type == 'homework':
        c.execute('SELECT date, score, max_score FROM homework WHERE student_id=? ORDER BY date', (student_id,))
        title, color = 'الواجبات', '#4361ee'
    else:
        c.execute('SELECT date, score, max_score FROM exams WHERE student_id=? ORDER BY date', (student_id,))
        title, color = 'الامتحانات', '#06d6a0'
    data = c.fetchall(); conn.close()
    fig, ax = plt.subplots(figsize=(9, 3.5))
    if not data:
        ax.text(0.5, 0.5, 'لا توجد بيانات', ha='center', va='center', fontsize=14); ax.axis('off')
    else:
        dates = [r[0] for r in data]; pcts = [round(r[1]/r[2]*100,1) for r in data]
        ax.bar(range(len(dates)), pcts, color=color, alpha=0.7, width=0.6, zorder=3)
        ax.plot(range(len(dates)), pcts, 'o-', color=color, linewidth=2, markersize=5, zorder=4)
        for i, pct in enumerate(pcts): ax.text(i, pct+2, f'{pct}%', ha='center', fontsize=8, fontweight='bold', color=color)
        ax.set_xticks(range(len(dates))); ax.set_xticklabels(dates, rotation=30, ha='right', fontsize=8)
        ax.set_ylim(0,115); ax.set_title(title, fontsize=13, fontweight='bold')
        ax.axhline(y=50, color='red', linestyle='--', alpha=0.3); ax.axhline(y=80, color='green', linestyle='--', alpha=0.3)
        ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False); ax.grid(axis='y', alpha=0.2, zorder=0)
    plt.tight_layout()
    img = BytesIO(); plt.savefig(img, format='png', dpi=120); img.seek(0); plt.close()
    return send_file(img, mimetype='image/png')

@app.route('/print_qr')
def print_qr():
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor(); c.execute('SELECT id, name, student_id, parent_whatsapp FROM students')
    students_list = c.fetchall(); conn.close()
    return render_template('print_qr.html', students=students_list)

@app.route('/ranking')
def ranking():
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor(); c.execute('SELECT id, name, student_id FROM students')
    students_list = c.fetchall(); ranking_data = []
    for s in students_list:
        sid = s[2]
        c.execute('SELECT COUNT(*) FROM attendance WHERE student_id=?', (sid,)); att = c.fetchone()[0]
        c.execute('SELECT score, max_score FROM homework WHERE student_id=?', (sid,)); hw = c.fetchall()
        c.execute('SELECT score, max_score FROM exams WHERE student_id=?', (sid,)); ex = c.fetchall()
        hw_avg = round(sum(r[0]/r[1]*100 for r in hw)/len(hw), 1) if hw else 0
        ex_avg = round(sum(r[0]/r[1]*100 for r in ex)/len(ex), 1) if ex else 0
        overall = round((hw_avg*0.3 + ex_avg*0.5 + min(att*10,100)*0.2), 1)
        ranking_data.append({'name':s[1],'student_id':sid,'attendance':att,'hw_avg':hw_avg,'ex_avg':ex_avg,'overall':overall})
    ranking_data.sort(key=lambda x: x['overall'], reverse=True)
    conn.close()
    return render_template('ranking.html', ranking=ranking_data)

@app.route('/schedule', methods=['GET','POST'])
def schedule():
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            c.execute('INSERT INTO schedule (day, time_from, time_to, subject, notes) VALUES (?,?,?,?,?)',
                (request.form['day'], request.form['time_from'], request.form['time_to'],
                 request.form['subject'], request.form.get('notes','')))
        elif action == 'delete':
            c.execute('DELETE FROM schedule WHERE id=?', (request.form['id'],))
        conn.commit()
    days_order = ['السبت','الأحد','الاثنين','الثلاثاء','الأربعاء','الخميس','الجمعة']
    c.execute('SELECT id, day, time_from, time_to, subject, notes FROM schedule ORDER BY time_from')
    rows = c.fetchall(); conn.close()
    schedule_dict = {d: [] for d in days_order}
    for r in rows:
        if r[1] in schedule_dict: schedule_dict[r[1]].append(r)
    return render_template('schedule.html', schedule=schedule_dict, days=days_order, now=datetime.now())

# ─── FEES ───────────────────────────────────────────────
@app.route('/fees', methods=['GET','POST'])
def fees():
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'delete':
            c.execute('DELETE FROM fees WHERE id=?', (request.form['id'],))
        elif action == 'manual_pay':
            # دفع يدوي (لو الطالب دفع بدون scan)
            c.execute('INSERT INTO fees (student_id, amount, description, created_at) VALUES (?,?,?,?)',
                (request.form['student_id'], float(request.form['amount']),
                 request.form.get('description','دفع يدوي'), datetime.now().strftime("%Y-%m-%d")))
        conn.commit()

    # بيانات كل طالب
    c.execute('SELECT id, name, student_id, session_fee FROM students ORDER BY name')
    all_students = c.fetchall()
    students_fees = []
    total_paid_all = 0
    total_sessions_all = 0
    for s in all_students:
        sid = s[2]
        fee = s[3] or 85
        c.execute('SELECT COUNT(*) FROM attendance WHERE student_id=?', (sid,))
        sessions = c.fetchone()[0]
        c.execute('SELECT COALESCE(SUM(amount),0) FROM fees WHERE student_id=?', (sid,))
        paid = c.fetchone()[0]
        total_paid_all += paid
        total_sessions_all += sessions
        students_fees.append({
            'id': s[0], 'name': s[1], 'student_id': sid,
            'session_fee': fee, 'sessions': sessions, 'paid': paid,
        })

    # سجل الدفعات
    c.execute('''SELECT f.id, s.name, f.student_id, f.amount, f.description, f.created_at
        FROM fees f LEFT JOIN students s ON f.student_id=s.student_id
        ORDER BY f.created_at DESC LIMIT 100''')
    payments = c.fetchall()
    conn.close()

    return render_template('fees.html',
        students_fees=students_fees,
        payments=payments,
        total_paid=total_paid_all,
        total_sessions=total_sessions_all)

# ─── SETTINGS ───────────────────────────────────────────
@app.route('/settings', methods=['GET','POST'])
def settings():
    if request.method == 'POST':
        conn = sqlite3.connect('attendance.db')
        c = conn.cursor()
        for key in ['session_fee', 'session_start', 'late_minutes',
                    'absence_alert', 'weekly_report_day',
                    'theme_primary', 'theme_success', 'theme_bg']:
            val = request.form.get(key)
            if val is not None:
                c.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, val))
        conn.commit(); conn.close()
        return redirect(url_for('settings'))
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute("SELECT key, value FROM settings")
    s = {r[0]: r[1] for r in c.fetchall()}; conn.close()
    return render_template('settings.html', s=s)

@app.route('/report')
def report():
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute('''SELECT a.id, s.name, a.student_id, a.date, a.time FROM attendance a
        LEFT JOIN students s ON a.student_id=s.student_id ORDER BY a.date DESC, a.time DESC''')
    data = c.fetchall(); conn.close()
    return render_template('report.html', data=data)

@app.route('/download_excel')
def download_excel():
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor(); c.execute('SELECT student_id, date, time FROM attendance')
    data = c.fetchall(); conn.close()
    df = pd.DataFrame(data, columns=['Student ID','Date','Time'])
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Attendance')
    output.seek(0)
    return send_file(output, as_attachment=True, download_name='attendance_report.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/chart')
def chart():
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute('SELECT date, COUNT(DISTINCT student_id) FROM attendance GROUP BY date ORDER BY date')
    chart_data = c.fetchall(); conn.close()
    dates = [r[0] for r in chart_data] or ['—']
    counts = [r[1] for r in chart_data] or [0]
    fig, ax = plt.subplots(figsize=(10,4))
    ax.fill_between(range(len(dates)), counts, alpha=0.15, color='#4361ee')
    ax.plot(range(len(dates)), counts, marker='o', color='#4361ee', linewidth=2.5, markersize=6)
    ax.set_xticks(range(len(dates))); ax.set_xticklabels(dates, rotation=30, ha='right', fontsize=9)
    ax.set_ylabel('عدد الطلاب', fontsize=10); ax.set_title('الحضور اليومي', fontsize=13, fontweight='bold')
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False); ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    img = BytesIO(); plt.savefig(img, format='png', dpi=120); img.seek(0); plt.close()
    return send_file(img, mimetype='image/png')

@app.route('/video_feed')
def video_feed():
    def gen():
        cap = cv2.VideoCapture(0)
        while True:
            ret, frame = cap.read()
            if not ret: break
            data, bbox, _ = detector.detectAndDecode(frame)
            if data:
                success, _ = mark_attendance(data.strip())
                color = (0,255,0) if success else (0,0,255)
                if bbox is not None:
                    bbox = bbox.astype(int)
                    for i in range(len(bbox[0])):
                        cv2.line(frame, tuple(bbox[0][i]), tuple(bbox[0][(i+1)%len(bbox[0])]), color, 2)
                cv2.putText(frame, 'OK' if success else 'Already', (50,50), cv2.FONT_HERSHEY_SIMPLEX, 1, color, 2)
            ret, buffer = cv2.imencode('.jpg', frame)
            yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
        cap.release()
    return Response(gen(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/scan_camera')
def scan_camera():
    return render_template('scan_camera.html')

# ─── PWA ────────────────────────────────────────────────
@app.route('/manifest.json')
def manifest():
    from flask import Response
    import json
    data = {
        "name": "نظام الحضور",
        "short_name": "الحضور",
        "description": "نظام متابعة حضور الطلاب",
        "start_url": "/dashboard",
        "display": "standalone",
        "background_color": "#0f172a",
        "theme_color": "#4361ee",
        "orientation": "portrait",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"}
        ]
    }
    return Response(json.dumps(data, ensure_ascii=False),
                    mimetype='application/manifest+json')

@app.route('/sw.js')
def service_worker():
    sw = """
const CACHE = 'attendance-v1';
const OFFLINE = ['/dashboard', '/scan', '/static/style.css'];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(OFFLINE)));
});
self.addEventListener('fetch', e => {
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});
"""
    from flask import Response
    return Response(sw, mimetype='application/javascript')

# ─── WEEKLY WHATSAPP REPORT ─────────────────────────────
@app.route('/weekly_report', methods=['GET', 'POST'])
def weekly_report():
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()

    # استعلام للحصول على بيانات الطلاب خلال آخر 7 أيام
    c.execute('''
        SELECT s.student_id, s.name, s.parent_whatsapp,
        COUNT(a.id) as sessions,
        COALESCE(SUM(CASE WHEN a.status="متأخر" THEN 1 ELSE 0 END),0) as late
        FROM students s
        LEFT JOIN attendance a ON a.student_id=s.student_id
            AND a.date >= date("now","-6 days")
        GROUP BY s.student_id
    ''')
    students_data = c.fetchall()
    conn.close()

    # تحويل البيانات إلى قائمة من القواميس لتسهيل الوصول في القالب
    students = []
    for student in students_data:
        student_id, name, phone, sessions, late = student
        absent = 7 - sessions  # تقريبي
        students.append({
            'student_id': student_id,
            'name': name,
            'phone': phone,
            'sessions': sessions,
            'late': late,
            'absent': absent
        })

    if request.method == 'POST':
        sent = 0
        for student in students:
            if not student['phone']:
                continue
            msg = (
                    f" تنبيه حضور أسبوعي "
                    f"───────────────────────────\n\n"
                    f" الطالب: {student['name']}  \n"
                    f" تفاصيل الحضور:  \n"
                    f" حضر: {student['sessions']} يوم\n"
                    f" متأخر: {student['late']} مرات \n"
                    f" غائب: {student['absent']}  ايام\n"
                    f"───────────────────────────\n"
                    f" التاريخ: {datetime.now().strftime('%Y-%m-%d')}\n"
                    f"───────────────────────────\n"
                    f" ملاحظة:\n"
                    f"نرجو المتابعة والاهتمام بحضور ابنكم.\n"
                    f"شكرًا لتعاونكم! "
                  )


            encoded_msg = msg.replace(' ', '%20').replace('\n', '%0A')
            webbrowser.open(f"https://wa.me/{phone}?text={encoded_msg}")
            sent += 1

        return jsonify({'success': True, 'sent': sent, 'message': f'تم فتح واتساب لـ {sent} ولي أمر'})

    return render_template('weekly_report.html', students=students)


# ─── ABSENCE ALERTS ─────────────────────────────────────
@app.route('/absence_alerts')
def absence_alerts():
    threshold = int(get_setting('absence_alert', '3'))
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    # إجمالي أيام الدراسة (أيام فيها حضور لأي طالب)
    c.execute("SELECT COUNT(DISTINCT date) FROM attendance")
    total_days = c.fetchone()[0]
    c.execute('''SELECT s.id, s.name, s.student_id, s.parent_whatsapp,
        COUNT(a.id) as attended,
        ? - COUNT(a.id) as absences
        FROM students s
        LEFT JOIN attendance a ON a.student_id=s.student_id
        GROUP BY s.student_id
        HAVING absences >= ?
        ORDER BY absences DESC''', (total_days, threshold))
    alerts = c.fetchall()
    conn.close()
    return render_template('absence_alerts.html',
                           alerts=alerts, threshold=threshold, total_days=total_days)

@app.route('/send_absence_alert/<student_id>')
def send_absence_alert(student_id):
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute("SELECT name, parent_whatsapp FROM students WHERE student_id=?", (student_id,))
    row = c.fetchone()
    c.execute("SELECT COUNT(DISTINCT date) FROM attendance"); total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM attendance WHERE student_id=?", (student_id,))
    attended = c.fetchone()[0]
    conn.close()
    if row and row[1]:
        absences = total - attended
        msg = (f" تنبيه غياب\n"
               f"الطالب {row[0]} غاب {absences} مرة\n"
               f"من أصل {total} حصة\n"
               f"يرجى التواصل للمتابعة ")
        webbrowser.open(f"https://wa.me/{row[1]}?text={msg}")
        return jsonify({'success': True})
    return jsonify({'success': False, 'message': 'لا يوجد رقم واتساب'})

# ─── HOLIDAYS ───────────────────────────────────────────
@app.route('/holidays', methods=['GET','POST'])
def holidays():
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            try:
                c.execute('INSERT INTO holidays (date, name) VALUES (?,?)',
                          (request.form['date'], request.form['name']))
                conn.commit()
            except: pass
        elif action == 'delete':
            c.execute('DELETE FROM holidays WHERE id=?', (request.form['id'],))
            conn.commit()
    c.execute('SELECT id, date, name FROM holidays ORDER BY date DESC')
    hols = c.fetchall(); conn.close()
    return render_template('holidays.html', holidays=hols,
                           today=datetime.now().strftime("%Y-%m-%d"))

@app.route('/holidays_json')
def holidays_json():
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute('SELECT date FROM holidays')
    dates = [r[0] for r in c.fetchall()]; conn.close()
    return jsonify(dates)

# ─── GLOBAL SEARCH ─────────────────────────────────────
@app.route('/search')
def global_search():
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({'students': [], 'attendance': []})
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute("""SELECT name, student_id, COALESCE(notes,'') FROM students
        WHERE name LIKE ? OR student_id LIKE ? LIMIT 8""",
        ('%'+q+'%', '%'+q+'%'))
    students = [{'name':r[0],'id':r[1],'notes':r[2]} for r in c.fetchall()]
    c.execute("""SELECT s.name, a.date, a.time, a.status FROM attendance a
        JOIN students s ON s.student_id=a.student_id
        WHERE s.name LIKE ? ORDER BY a.date DESC LIMIT 5""", ('%'+q+'%',))
    attendance = [{'name':r[0],'date':r[1],'time':r[2],'status':r[3]} for r in c.fetchall()]
    conn.close()
    return jsonify({'students': students, 'attendance': attendance})

# ─── NOTIFICATIONS ──────────────────────────────────────
def add_notification(type_, title, body, link=''):
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute("INSERT INTO notifications (type,title,body,link,created_at) VALUES (?,?,?,?,?)",
              (type_, title, body, link, datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit(); conn.close()

@app.route('/notifications')
def notifications():
    conn = sqlite3.connect('attendance.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id, type, title, body, link, is_read, created_at FROM notifications ORDER BY created_at DESC')
    notifs = cursor.fetchall()
    conn.close()
    return render_template('notifications.html', notifs=notifs)

@app.route('/notifications_data')
def notifications_data():
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM notifications WHERE is_read=0")
    unread = c.fetchone()[0]
    c.execute("SELECT id,type,title,body,link,created_at FROM notifications ORDER BY id DESC LIMIT 8")
    items = [{'id':r[0],'type':r[1],'title':r[2],'body':r[3],'link':r[4],'time':r[5]} for r in c.fetchall()]
    conn.close()
    return jsonify({'unread': unread, 'items': items})

@app.route('/notifications/read_all', methods=['POST'])
def notifications_read_all():
    conn = sqlite3.connect('attendance.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE notifications SET is_read=1")
    conn.commit()
    conn.close()
    return redirect(url_for('notifications'))




@app.route('/notifications/delete/<int:notification_id>', methods=['POST'])
def delete_notification(notification_id):
    conn = sqlite3.connect('attendance.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM notifications WHERE id=?', (notification_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('notifications'))




# ─── STUDENT PDF REPORT ─────────────────────────────────
@app.route('/student_pdf/<student_id>')
def student_pdf(student_id):
    """تقرير HTML قابل للطباعة كـ PDF"""
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute("SELECT id,name,student_id,parent_whatsapp,session_fee,notes FROM students WHERE student_id=?", (student_id,))
    student = c.fetchone()
    if not student:
        conn.close(); return "طالب غير موجود", 404
    c.execute("SELECT date,time,status FROM attendance WHERE student_id=? ORDER BY date DESC", (student_id,))
    att = c.fetchall()
    c.execute("SELECT date,score,max_score FROM homework WHERE student_id=? ORDER BY date DESC", (student_id,))
    hw = c.fetchall()
    c.execute("SELECT date,score,max_score FROM exams WHERE student_id=? ORDER BY date DESC", (student_id,))
    exams = c.fetchall()
    c.execute("SELECT COALESCE(SUM(amount),0) FROM fees WHERE student_id=?", (student_id,))
    total_paid = c.fetchone()[0]
    conn.close()
    att_pct = round(len(att) / max(len(att), 1) * 100)
    hw_avg  = round(sum(r[1]/r[2]*100 for r in hw)/len(hw)) if hw else 0
    ex_avg  = round(sum(r[1]/r[2]*100 for r in exams)/len(exams)) if exams else 0
    return render_template('student_pdf.html',
        student=student, att=att, hw=hw, exams=exams,
        total_paid=int(total_paid), att_pct=att_pct,
        hw_avg=hw_avg, ex_avg=ex_avg,
        print_date=datetime.now().strftime("%Y-%m-%d"))

# ─── GROUP COMPARISON ───────────────────────────────────
@app.route('/groups_compare')
def groups_compare():
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute("""SELECT g.name,
        COUNT(DISTINCT s.id) as student_count,
        COUNT(DISTINCT a.id) as total_att,
        ROUND(AVG(CASE WHEN a.id IS NOT NULL THEN 1.0 ELSE 0 END)*100,1) as att_pct,
        COALESCE(AVG(h.score*100.0/h.max_score),0) as hw_avg,
        COALESCE(AVG(e.score*100.0/e.max_score),0) as ex_avg
        FROM groups g
        LEFT JOIN students s ON s.group_id=g.id
        LEFT JOIN attendance a ON a.student_id=s.student_id
        LEFT JOIN homework h ON h.student_id=s.student_id
        LEFT JOIN exams e ON e.student_id=s.student_id
        GROUP BY g.id ORDER BY att_pct DESC""")
    groups = c.fetchall()
    conn.close()
    import json
    labels  = json.dumps([g[0] for g in groups], ensure_ascii=False)
    att_data= json.dumps([round(g[3] or 0, 1) for g in groups])
    hw_data = json.dumps([round(g[4] or 0, 1) for g in groups])
    ex_data = json.dumps([round(g[5] or 0, 1) for g in groups])
    return render_template('groups_compare.html',
        groups=groups, labels=labels,
        att_data=att_data, hw_data=hw_data, ex_data=ex_data)

# ─── MONTHLY FINANCIAL REPORT ───────────────────────────
@app.route('/monthly_finance')
def monthly_finance():
    month = request.args.get('month', datetime.now().strftime("%Y-%m"))
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute("""SELECT s.name, s.session_fee,
        COUNT(a.id) as sessions,
        COUNT(a.id) * s.session_fee as expected,
        COALESCE((SELECT SUM(f.amount) FROM fees f
            WHERE f.student_id=s.student_id
            AND strftime('%Y-%m', f.created_at)=?), 0) as paid
        FROM students s
        LEFT JOIN attendance a ON a.student_id=s.student_id
            AND strftime('%Y-%m', a.date)=?
        GROUP BY s.id ORDER BY sessions DESC""", (month, month))
    rows = c.fetchall()
    total_expected  = sum(r[3] for r in rows)
    total_paid      = sum(r[4] for r in rows)
    total_sessions  = sum(r[2] for r in rows)
    c.execute("""SELECT strftime('%Y-%m', created_at) as m FROM fees
        GROUP BY m ORDER BY m DESC LIMIT 24""")
    months_list = [r[0] for r in c.fetchall()]
    # نضيف الشهر الحالي لو مش موجود
    if month not in months_list:
        months_list.insert(0, month)
    conn.close()
    return render_template('monthly_finance.html',
        rows=rows, month=month, months=months_list,
        total_sessions=total_sessions,
        total_expected=int(total_expected),
        total_paid=int(total_paid),
        diff=int(total_paid - total_expected))


# ─── DAILY REPORT TO PARENT ─────────────────────────────
@app.route('/send_daily_report/<student_id>')
def send_daily_report(student_id):
    today = datetime.now().strftime("%Y-%m-%d")
    day_ar = ['الاثنين','الثلاثاء','الأربعاء','الخميس','الجمعة','السبت','الأحد'][datetime.now().weekday()]

    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()

    # بيانات الطالب
    c.execute('''SELECT s.name, s.parent_whatsapp, s.session_fee,
        COALESCE(g.name,'') FROM students s
        LEFT JOIN groups g ON g.id=s.group_id
        WHERE s.student_id=?''', (student_id,))
    row = c.fetchone()
    if not row or not row[1]:
        conn.close()
        return jsonify({'success': False, 'message': 'لا يوجد رقم واتساب لولي الأمر'})

    name, phone, fee, group = row

    # حضور اليوم
    c.execute("SELECT time, status FROM attendance WHERE student_id=? AND date=?", (student_id, today))
    att = c.fetchone()

    # واجب اليوم
    c.execute("SELECT done FROM homework WHERE student_id=? AND date=?", (student_id, today))
    hw = c.fetchone()

    # آخر امتحان
    c.execute("SELECT score, max_score, date FROM exams WHERE student_id=? ORDER BY date DESC LIMIT 1", (student_id,))
    exam = c.fetchone()

    # إجمالي الحصص
    c.execute("SELECT COUNT(*) FROM attendance WHERE student_id=?", (student_id,))
    total_att = c.fetchone()[0]

    conn.close()

    # ── بناء الرسالة ──────────────────────────────────
    lines = []
    lines.append(f" تقرير حصة {day_ar}")
    lines.append(f"الطالب: {name}")
    if group:
        lines.append(f"المجموعة: {group}")
    lines.append("──────────────────")

    # الحضور
    if att:
        att_time, att_status = att
        if att_status == 'متأخر':
            lines.append(f" الحضور: حضر متأخراً — الساعة {att_time[:5]}")
        else:
            lines.append(f" الحضور: حضر في الوقت — الساعة {att_time[:5]}")
    else:
        lines.append("❌ الحضور: غائب اليوم")

    # الواجب
    if hw is not None:
        if hw[0]:
            lines.append("✅ الواجب: عمله")
        else:
            lines.append("❌ الواجب: لم يعمله")
    else:
        lines.append("📝 الواجب: لم يُسجَّل بعد")

    # آخر امتحان
    if exam:
        score, max_score, exam_date = exam
        pct = round(score / max_score * 100) if max_score else 0
        if pct >= 80:
            grade_emoji = "🌟"
        elif pct >= 60:
            grade_emoji = "👍"
        else:
            grade_emoji = "⚠️"
        lines.append(f"{grade_emoji} آخر امتحان ({exam_date}): {score}/{max_score} — {pct}%")

    lines.append("──────────────────")
    lines.append(f" إجمالي الحصص: {total_att} حصة")
    lines.append(f" رسوم الحصة: {int(fee or 85)} ج")
    lines.append("")
    lines.append("شكراً لمتابعتكم ")

    msg = '\n'.join(lines)
    url = f"https://wa.me/{phone}?text={urllib.parse.quote(msg)}"
    webbrowser.open(url)
    return jsonify({'success': True, 'message': f'✅ تم فتح واتساب لولي أمر {name}'})


# ─── TODAY ABSENT LIST ──────────────────────────────────
@app.route('/absent_today')
def absent_today():
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute("""SELECT s.name, s.student_id, s.parent_whatsapp,
        COALESCE(g.name,'—') as grp,
        (SELECT COUNT(*) FROM attendance WHERE student_id=s.student_id) as total_att,
        (SELECT COUNT(DISTINCT date) FROM attendance) as total_sessions
        FROM students s
        LEFT JOIN groups g ON g.id=s.group_id
        WHERE s.student_id NOT IN (
            SELECT student_id FROM attendance WHERE date=?
        )
        ORDER BY s.name""", (today,))
    absent = c.fetchall()
    conn.close()
    today_display = datetime.now().strftime("%d/%m/%Y")
    day_ar = ['الاثنين','الثلاثاء','الأربعاء','الخميس','الجمعة','السبت','الأحد'][datetime.now().weekday()]
    return render_template('absent_today.html',
        absent=absent, today=today_display,
        day_ar=day_ar, count=len(absent))

@app.route('/send_warning/<student_id>')
def send_warning(student_id):
    today = datetime.now().strftime("%Y-%m-%d")
    day_ar = ['الاثنين','الثلاثاء','الأربعاء','الخميس','الجمعة','السبت','الأحد'][datetime.now().weekday()]
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute("""SELECT s.name, s.parent_whatsapp,
        (SELECT COUNT(*) FROM attendance WHERE student_id=s.student_id) as att,
        (SELECT COUNT(DISTINCT date) FROM attendance) as total
        FROM students s WHERE s.student_id=?""", (student_id,))
    row = c.fetchone()
    conn.close()
    if not row or not row[1]:
        return jsonify({'success': False, 'message': 'لا يوجد رقم واتساب'})
    name, phone, att, total = row
    absences = max(0, total - att)
    msg = (
        f" تنبيه غياب — {day_ar}\n"
        f"──────────────────\n"
        f"الطالب: {name}\n"
        f"غائب اليوم ولم يحضر الحصة\n"
        f"──────────────────\n"
        f" إجمالي الغياب: {absences} مرة\n"
        f" الحضور: {att} من {total} حصة\n"
        f"──────────────────\n"
        f"نرجو المتابعة والاهتمام بحضور ابنكم "
    )
    webbrowser.open(f"https://wa.me/{phone}?text={urllib.parse.quote(msg)}")
    return jsonify({'success': True, 'message': f'تم إرسال تحذير لولي أمر {name}'})


# ─── IMPORT FROM EXCEL ──────────────────────────────────
@app.route('/import_students', methods=['GET','POST'])
def import_students():
    if request.method == 'GET':
        return render_template('import_students.html')

    # ── معاينة بدون حفظ ──────────────────────────────
    if 'preview' in request.form:
        file = request.files.get('file')
        if not file or not file.filename:
            return render_template('import_students.html', error='اختر ملف Excel أولاً')
        try:
            df = pd.read_excel(file)
            df.columns = [str(c).strip().lower() for c in df.columns]

            # خرائط أسماء الأعمدة المقبولة
            col_map = {
                'name':   ['name','الاسم','اسم الطالب','اسم'],
                'id':     ['id','student_id','رقم الطالب','رقم','كود'],
                'phone':  ['phone','whatsapp','parent_whatsapp','رقم ولي الامر',
                           'واتساب','رقم الواتساب','رقم ولي الأمر'],
                'fee':    ['fee','session_fee','سعر الحصة','الرسوم','سعر'],
                'spw':    ['sessions_per_week','حصص اسبوعيا','حصص/اسبوع',
                           'حصص في الاسبوع','عدد الحصص'],
                'group':  ['group','group_name','المجموعة','مجموعة'],
                'notes':  ['notes','ملاحظات','note'],
            }

            def find_col(df, keys):
                for k in keys:
                    if k in df.columns:
                        return k
                return None

            c_name  = find_col(df, col_map['name'])
            c_id    = find_col(df, col_map['id'])
            c_phone = find_col(df, col_map['phone'])
            c_fee   = find_col(df, col_map['fee'])
            c_spw   = find_col(df, col_map['spw'])
            c_group = find_col(df, col_map['group'])
            c_notes = find_col(df, col_map['notes'])

            if not c_name or not c_id:
                return render_template('import_students.html',
                    error='الملف لازم يكون فيه عمود الاسم وعمود الـ ID على الأقل',
                    columns=list(df.columns))

            rows = []
            errors = []
            for i, row in df.iterrows():
                name = str(row[c_name]).strip() if c_name else ''
                sid  = str(row[c_id]).strip()   if c_id  else ''
                # تجاهل الصفوف الفاضية
                if not name or not sid or name.lower() in ('nan','') or sid.lower() in ('nan',''):
                    continue
                # تنظيف الـ ID من .0 لو Excel حوّله لرقم
                if sid.endswith('.0'):
                    sid = sid[:-2]
                rows.append({
                    'name':  name,
                    'id':    sid,
                    'phone': str(row[c_phone]).strip() if c_phone and str(row[c_phone]) not in ('nan','None','') else '',
                    'fee':   int(float(row[c_fee])) if c_fee and str(row[c_fee]) not in ('nan','None','') else 85,
                    'spw':   int(float(row[c_spw])) if c_spw and str(row[c_spw]) not in ('nan','None','') else 1,
                    'group': str(row[c_group]).strip() if c_group and str(row[c_group]) not in ('nan','None','') else '',
                    'notes': str(row[c_notes]).strip() if c_notes and str(row[c_notes]) not in ('nan','None','') else '',
                })

            # تحقق من التكرار مع الـ DB
            conn = sqlite3.connect('attendance.db')
            c = conn.cursor()
            c.execute('SELECT student_id FROM students')
            existing = {r[0] for r in c.fetchall()}
            conn.close()

            for r in rows:
                r['exists'] = r['id'] in existing

            import json
            preview_json = json.dumps(rows, ensure_ascii=False)
            return render_template('import_students.html',
                rows=rows, preview_json=preview_json,
                total=len(rows),
                new_count=sum(1 for r in rows if not r['exists']),
                dup_count=sum(1 for r in rows if r['exists']))

        except Exception as e:
            return render_template('import_students.html', error=f'خطأ في قراءة الملف: {e}')

    # ── حفظ فعلي ──────────────────────────────────────
    if 'confirm' in request.form:
        import json
        rows = json.loads(request.form.get('preview_json', '[]'))
        skip_existing = 'skip_existing' in request.form

        conn = sqlite3.connect('attendance.db')
        c = conn.cursor()
        c.execute('SELECT student_id FROM students')
        existing = {r[0] for r in c.fetchall()}
        c.execute('SELECT id, name FROM groups')
        groups_map = {r[1].strip(): r[0] for r in c.fetchall()}

        added = skipped = 0
        for r in rows:
            if r['id'] in existing:
                if skip_existing:
                    skipped += 1
                    continue
                else:
                    # تحديث بيانات الموجود
                    c.execute("""UPDATE students SET name=?, parent_whatsapp=?,
                        session_fee=?, sessions_per_week=?, notes=? WHERE student_id=?""",
                        (r['name'], r['phone'], r['fee'], r['spw'], r['notes'], r['id']))
                    skipped += 1
                    continue

            # إضافة مجموعة جديدة لو مش موجودة
            grp_id = None
            if r['group']:
                if r['group'] not in groups_map:
                    c.execute('INSERT INTO groups (name) VALUES (?)', (r['group'],))
                    grp_id = c.lastrowid
                    groups_map[r['group']] = grp_id
                else:
                    grp_id = groups_map[r['group']]

            try:
                c.execute("""INSERT INTO students
                    (name, student_id, parent_whatsapp, session_fee,
                     sessions_per_week, group_id, notes)
                    VALUES (?,?,?,?,?,?,?)""",
                    (r['name'], r['id'], r['phone'], r['fee'],
                     r['spw'], grp_id, r['notes']))
                generate_qr_code(r['id'], r['name'])
                added += 1
            except Exception:
                skipped += 1

        conn.commit(); conn.close()

        # إشعار داخلي
        try:
            conn2 = sqlite3.connect('attendance.db')
            conn2.cursor().execute(
                "INSERT INTO notifications (type,title,body,link,created_at) VALUES (?,?,?,?,?)",
                ('import', '📥 استيراد طلاب',
                 f'تم إضافة {added} طالب جديد من Excel',
                 '/students', datetime.now().strftime("%Y-%m-%d %H:%M")))
            conn2.commit(); conn2.close()
        except: pass

        return render_template('import_students.html',
            success=True, added=added, skipped=skipped)

    return redirect(url_for('import_students'))


@app.route('/download_template')
def download_template():
    """تحميل نموذج Excel فاضي للاستيراد"""
    import io
    df = pd.DataFrame(columns=[
        'name / الاسم',
        'id / student_id',
        'phone / واتساب',
        'fee / سعر الحصة',
        'sessions_per_week / حصص اسبوعيا',
        'group / المجموعة',
        'notes / ملاحظات'
    ])
    # صف مثال
    df.loc[0] = ['أحمد محمد', '2025001', '201012345678', 85, 1, 'A', '']
    df.loc[1] = ['سارة علي',  '2025002', '201098765432', 100, 2, 'B', 'طالبة متميزة']

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='الطلاب')
        wb = writer.book
        ws = writer.sheets['الطلاب']
        fmt_header = wb.add_format({'bold': True, 'bg_color': '#4361ee',
                                    'font_color': 'white', 'border': 1})
        for col_num, col in enumerate(df.columns):
            ws.write(0, col_num, col, fmt_header)
            ws.set_column(col_num, col_num, max(18, len(col) + 2))
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name='نموذج_استيراد_الطلاب.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


if __name__ == '__main__':
    init_db()
    add_sample_students()
    create_default_users()
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor(); c.execute('SELECT student_id, name FROM students')
    for sid, name in c.fetchall():
        if not os.path.exists(f'static/qr_codes/{sid}.png'):
            generate_qr_code(sid, name)
    conn.close()
    
    port = int(os.environ.get('PORT', 5000))
# C:\Users\Mina\AppData\Local\ngrok>
# ngrok http 5000
