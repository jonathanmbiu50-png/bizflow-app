import os
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import Flask, request, jsonify, session, render_template, redirect
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import func

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-this-before-production')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///bizflow.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('COOKIE_SECURE', '0') == '1'
db = SQLAlchemy(app)

PLANS = {'Basic': 999, 'Business': 2499, 'Enterprise': 5999}

class Business(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(140), nullable=False)
    plan = db.Column(db.String(30), default='Basic')
    status = db.Column(db.String(30), default='trial')
    trial_ends = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=14))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey('business.id'), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(180), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), default='owner')

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey('business.id'), nullable=False, index=True)
    name = db.Column(db.String(140), nullable=False)
    sku = db.Column(db.String(80))
    category = db.Column(db.String(80), default='General')
    price = db.Column(db.Float, nullable=False, default=0)
    cost = db.Column(db.Float, nullable=False, default=0)
    stock = db.Column(db.Integer, nullable=False, default=0)
    low_stock = db.Column(db.Integer, default=5)

class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey('business.id'), nullable=False, index=True)
    name = db.Column(db.String(140), nullable=False)
    phone = db.Column(db.String(50), default='')
    email = db.Column(db.String(180), default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey('business.id'), nullable=False, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'))
    total = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    items = db.relationship('SaleItem', cascade='all, delete-orphan', backref='sale')

class SaleItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey('sale.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    product_name = db.Column(db.String(140), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Float, nullable=False)

class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey('business.id'), nullable=False, index=True)
    category = db.Column(db.String(80), nullable=False)
    description = db.Column(db.String(180), default='')
    amount = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


def current_user():
    uid = session.get('user_id')
    return db.session.get(User, uid) if uid else None

def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not current_user(): return jsonify({'error':'Please sign in'}), 401
        return fn(*args, **kwargs)
    return wrapped

def payload(): return request.get_json(silent=True) or request.form.to_dict()
def money(v): return round(float(v or 0), 2)

@app.get('/')
def home(): return render_template('index.html')

@app.post('/api/register')
def register():
    d = payload(); name=(d.get('name') or '').strip(); email=(d.get('email') or '').strip().lower(); password=d.get('password') or ''; business=(d.get('business') or '').strip()
    if not all([name,email,password,business]): return jsonify(error='Complete all fields'), 400
    if len(password) < 8: return jsonify(error='Password must be at least 8 characters'), 400
    if User.query.filter_by(email=email).first(): return jsonify(error='An account with this email already exists'), 409
    b=Business(name=business); db.session.add(b); db.session.flush()
    u=User(business_id=b.id,name=name,email=email,password_hash=generate_password_hash(password),role='owner')
    db.session.add(u); db.session.commit(); session['user_id']=u.id
    return jsonify(ok=True)

@app.post('/api/login')
def login():
    d=payload(); email=(d.get('email') or '').strip().lower(); u=User.query.filter_by(email=email).first()
    if not u or not check_password_hash(u.password_hash,d.get('password') or ''): return jsonify(error='Email or password is incorrect'), 401
    session.clear(); session['user_id']=u.id; return jsonify(ok=True)

@app.post('/api/logout')
def logout(): session.clear(); return jsonify(ok=True)

@app.get('/api/me')
@login_required
def me():
    u=current_user(); b=db.session.get(Business,u.business_id)
    return jsonify(user={'name':u.name,'email':u.email,'role':u.role},business={'name':b.name,'plan':b.plan,'status':b.status,'trial_ends':b.trial_ends.isoformat() if b.trial_ends else None})

@app.get('/api/dashboard')
@login_required
def dashboard():
    u=current_user(); bid=u.business_id
    sales= db.session.query(func.coalesce(func.sum(Sale.total),0)).filter_by(business_id=bid).scalar()
    expenses=db.session.query(func.coalesce(func.sum(Expense.amount),0)).filter_by(business_id=bid).scalar()
    products=Product.query.filter_by(business_id=bid).all()
    low=sum(1 for p in products if p.stock <= p.low_stock)
    recent=Sale.query.filter_by(business_id=bid).order_by(Sale.created_at.desc()).limit(6).all()
    daily=[]
    for i in range(6,-1,-1):
        day=(datetime.utcnow()-timedelta(days=i)).date()
        total=db.session.query(func.coalesce(func.sum(Sale.total),0)).filter(Sale.business_id==bid,func.date(Sale.created_at)==day.isoformat()).scalar()
        daily.append({'day':day.strftime('%a'),'sales':money(total)})
    return jsonify(metrics={'sales':money(sales),'expenses':money(expenses),'profit':money(sales-expenses),'products':len(products),'low_stock':low},daily=daily,recent=[{'id':s.id,'total':s.total,'date':s.created_at.strftime('%d %b %Y, %H:%M')} for s in recent])

@app.get('/api/products')
@login_required
def products():
    bid=current_user().business_id
    return jsonify(items=[{'id':p.id,'name':p.name,'sku':p.sku or '', 'category':p.category,'price':p.price,'cost':p.cost,'stock':p.stock,'low_stock':p.low_stock} for p in Product.query.filter_by(business_id=bid).order_by(Product.name).all()])

@app.post('/api/products')
@login_required
def add_product():
    d=payload(); name=(d.get('name') or '').strip()
    if not name: return jsonify(error='Product name is required'),400
    try: price=money(d.get('price')); cost=money(d.get('cost')); stock=int(d.get('stock',0)); threshold=int(d.get('low_stock',5))
    except (ValueError,TypeError): return jsonify(error='Enter valid numbers'),400
    if price<0 or cost<0 or stock<0 or threshold<0: return jsonify(error='Values cannot be negative'),400
    p=Product(business_id=current_user().business_id,name=name,sku=d.get('sku',''),category=d.get('category','General'),price=price,cost=cost,stock=stock,low_stock=threshold)
    db.session.add(p); db.session.commit(); return jsonify(ok=True,id=p.id)

@app.patch('/api/products/<int:pid>')
@login_required
def update_product(pid):
    p=Product.query.filter_by(id=pid,business_id=current_user().business_id).first_or_404(); d=payload()
    for key in ('name','sku','category'):
        if key in d: setattr(p,key,str(d[key]).strip())
    for key in ('price','cost'):
        if key in d: setattr(p,key,money(d[key]))
    for key in ('stock','low_stock'):
        if key in d: setattr(p,key,max(0,int(d[key])))
    db.session.commit(); return jsonify(ok=True)

@app.get('/api/customers')
@login_required
def customers():
    bid=current_user().business_id
    rows=Customer.query.filter_by(business_id=bid).order_by(Customer.name).all()
    return jsonify(items=[{'id':c.id,'name':c.name,'phone':c.phone,'email':c.email,'purchases':Sale.query.filter_by(business_id=bid,customer_id=c.id).count()} for c in rows])

@app.post('/api/customers')
@login_required
def add_customer():
    d=payload(); name=(d.get('name') or '').strip()
    if not name: return jsonify(error='Customer name is required'),400
    c=Customer(business_id=current_user().business_id,name=name,phone=d.get('phone',''),email=d.get('email',''))
    db.session.add(c); db.session.commit(); return jsonify(ok=True,id=c.id)

@app.post('/api/sales')
@login_required
def create_sale():
    d=payload(); bid=current_user().business_id; lines=d.get('items') or []
    if not lines: return jsonify(error='Add at least one item'),400
    customer_id=d.get('customer_id') or None
    if customer_id and not Customer.query.filter_by(id=customer_id,business_id=bid).first(): return jsonify(error='Invalid customer'),400
    try:
        prepared=[]; total=0
        for line in lines:
            p=Product.query.filter_by(id=int(line['product_id']),business_id=bid).with_for_update().first()
            qty=int(line.get('quantity',1))
            if not p or qty<1: raise ValueError('Invalid item or quantity')
            if p.stock<qty: raise ValueError(f'Not enough stock for {p.name}')
            prepared.append((p,qty)); total += p.price*qty
        sale=Sale(business_id=bid,customer_id=customer_id,total=money(total)); db.session.add(sale); db.session.flush()
        for p,qty in prepared:
            p.stock-=qty; db.session.add(SaleItem(sale_id=sale.id,product_id=p.id,product_name=p.name,quantity=qty,unit_price=p.price))
        db.session.commit(); return jsonify(ok=True,receipt={'id':sale.id,'total':sale.total,'date':sale.created_at.strftime('%d %b %Y, %H:%M'),'items':[{'name':p.name,'qty':q,'price':p.price} for p,q in prepared]})
    except (ValueError,KeyError,TypeError) as e:
        db.session.rollback(); return jsonify(error=str(e)),400

@app.get('/api/sales')
@login_required
def sales():
    bid=current_user().business_id
    rows=Sale.query.filter_by(business_id=bid).order_by(Sale.created_at.desc()).limit(100).all()
    return jsonify(items=[{'id':s.id,'total':s.total,'date':s.created_at.strftime('%d %b %Y, %H:%M'),'customer':db.session.get(Customer,s.customer_id).name if s.customer_id and db.session.get(Customer,s.customer_id) else 'Walk-in'} for s in rows])

@app.get('/api/plans')
def plans(): return jsonify(plans=[{'name':n,'price':p} for n,p in PLANS.items()])

@app.post('/api/subscription/choose')
@login_required
def choose_plan():
    d=payload(); name=d.get('plan')
    if name not in PLANS: return jsonify(error='Choose a valid plan'),400
    b=db.session.get(Business,current_user().business_id); b.plan=name
    # Payment is deliberately not marked paid: provider checkout must confirm payment server-side.
    db.session.commit(); return jsonify(ok=True,plan=name,message='Plan selected. Connect a payment provider to activate recurring billing.')

@app.get('/api/admin/overview')
@login_required
def admin_overview():
    if current_user().role!='platform_admin': return jsonify(error='Forbidden'),403
    return jsonify(businesses=Business.query.count(),users=User.query.count(),subscriptions={k:Business.query.filter_by(plan=k).count() for k in PLANS})

@app.cli.command('seed-demo')
def seed_demo():
    db.create_all()
    if User.query.filter_by(email='demo@bizflow.local').first(): print('Demo already exists'); return
    b=Business(name='Acme Retail Demo',plan='Business',status='trial'); db.session.add(b); db.session.flush()
    u=User(business_id=b.id,name='Demo Owner',email='demo@bizflow.local',password_hash=generate_password_hash('Demo12345!'),role='owner'); db.session.add(u)
    samples=[('Maize Flour 2kg','GRO-001','Groceries',180,145,42,10),('Cooking Oil 1L','GRO-002','Groceries',320,270,18,8),('Sugar 1kg','GRO-003','Groceries',165,140,6,10),('Rice 5kg','GRO-004','Groceries',950,810,24,5),('Laundry Soap','HOM-001','Household',120,85,55,10)]
    for n,sku,cat,price,cost,stock,low in samples: db.session.add(Product(business_id=b.id,name=n,sku=sku,category=cat,price=price,cost=cost,stock=stock,low_stock=low))
    for n,phone,email in [('Mary Wanjiku','0712345678','mary@example.com'),('Peter Mwangi','0723456789','peter@example.com'),('Amina Hassan','0734567890','amina@example.com')]: db.session.add(Customer(business_id=b.id,name=n,phone=phone,email=email))
    db.session.commit(); print('Demo account: demo@bizflow.local / Demo12345!')

with app.app_context(): db.create_all()

if __name__=='__main__': app.run(debug=os.environ.get('FLASK_DEBUG')=='1',host='0.0.0.0',port=int(os.environ.get('PORT',5000)))
