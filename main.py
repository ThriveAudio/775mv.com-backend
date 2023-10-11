import time
import uuid
#from pymongo import MongoClient
from motor import motor_asyncio
from fastapi import FastAPI, Request
from pprint import pprint
from json import loads
import re

app = FastAPI()

class SiteDB:
    def __init__(self):
        #self.db = MongoClient('localhost', 27017)['775mv_dev']
        self.db = motor_asyncio.AsyncIOMotorClient('localhost', 27017)['775mv_dev']

    async def get_collection_as_list(self, collection: str):
        documents = []
        db_collection = self.db[collection]
        for i in await db_collection.find():
            i['_id'] = str(i['_id'])
            documents.append(i)
        return documents

    async def get_document(self, collection: str, document: dict):
        documents = self.db[collection]
        doc = await documents.find_one(document)
        doc['_id'] = str(doc['_id'])
        return doc

    async def post_document(self, collection: str, document: dict):
        documents = self.db[collection]
        return await documents.insert_one(document)

db = SiteDB()

@app.get("/")
async def root():
    return {"message": "Hello World"}


@app.get("/hello/{name}")
async def say_hello(name: str):
    return {"message": f"Hello {name}"}


@app.get("/get-products")
async def get_products():
    #print(db.get_collection_as_list('product-information'))
    required_fields = ['_id', 'sku', 'name', 'price', 'description']
    checked_docs = []
    for i in await db.get_collection_as_list('product-information'):
        checked = True
        for field in required_fields:
            if field not in i.keys():
                checked = False
        if checked:
            checked_docs.append(i)
    for i, x in enumerate(checked_docs):
        x['id'] = i
    pprint(checked_docs)
    time.sleep(3)
    return checked_docs#db.get_collection_as_list('product-information')
    #return {'products': [{'name': 'filter', 'price': 20}, {'name': 'filter2', 'price': 10}]}


@app.get("/get-product/{sku}")
async def product(sku: str):
    print(sku)
    doc = await db.get_document('product-information', {'sku': sku})
    with open('static/pen_holder/desc.md') as f:
        doc['desc'] = f.read()
    with open('static/pen_holder/specs.md') as f:
        doc['specs'] = f.read()

    return doc


@app.get("/session-id")
async def new_session_id():
    doc = await db.post_document('accounts', {
        "email": "",
        "password": "",
        "cart": [],
        "orders": []
    })

    uid = str(uuid.uuid4())

    await db.post_document('sessions', {
        "id": uid,
        "account": doc.inserted_id
    })

    return {"sessionId": uid}


@app.post("/add-to-cart", status_code=200)
async def add_to_cart(request: Request):
    res = await request.body()
    res = loads(res.decode())
    session = await db.get_document('sessions', {'id': res['sessionId']})
    account_id = session['account']
    account = await db.get_document('accounts', {'_id': account_id})

    cart_index = -1
    for i, x in enumerate(account['cart']):
        if x['sku'] == res['sku']:
            cart_index = i

    if cart_index != -1:
        account['cart'][cart_index]['amount'] += res['amount']
    else:
        account['cart'].append({'sku': res['sku'], 'amount': res['amount'], 'checkout': True})

    await db.db['accounts'].update_one({'_id': account_id}, {'$set': {'cart': account['cart']}})


    #print(loads(res.decode()))
    return res

@app.post("/cart", status_code=200)
async def get_cart(request: Request):
    res = await request.body()
    print(res)
    res = loads(res.decode())
    print(res)
    if "sessionId" not in res.keys():
        return []
    session = await db.get_document('sessions', {'id': res['sessionId']})
    account_id = session['account']
    account = await db.get_document('accounts', {'_id': account_id})

    for item in account['cart']:
        db_item = await db.get_document('product-information', {'sku': item['sku']})
        item['price'] = db_item['price']
        item['name'] = db_item['name']
        item['description'] = db_item['description']

    return account['cart']

@app.post("/update-cart")
async def update_cart(request: Request):
    result = "ok"
    res = await request.body()
    print(res)
    res = loads(res.decode())
    print(res)
    session = await db.get_document('sessions', {'id': res['sessionId']})
    account_id = session['account']
    account = await db.get_document('accounts', {'_id': account_id})

    if res['type'] == "checkout":
        for item in account['cart']:
            if item['sku'] == res['sku']:
                item['checkout'] = res['value']
                break
        await db.db['accounts'].update_one({'_id': account_id}, {'$set': {'cart': account['cart']}})
    elif res['type'] == "amount":
        amount = 1
        if res['value'].isdigit():
            print("isdigit")
            amount = int(res['value'])
            if amount < 1:
                result = "denied"
            else:
                for item in account['cart']:
                    if item['sku'] == res['sku']:
                        item['amount'] = amount
                        break
                await db.db['accounts'].update_one({'_id': account_id}, {'$set': {'cart': account['cart']}})
        elif res['value'] == "":
            for item in account['cart']:
                if item['sku'] == res['sku']:
                    item['amount'] = 1
                    break
        else:
            result = "denied"
    elif res['type'] == "delete":
        i = 0
        deleted = False
        while i < len(account['cart']) and not deleted:
            if account['cart'][i]['sku'] == res['sku']:
                account['cart'].pop(i)
                deleted = True
            i += 1
        await db.db['accounts'].update_one({'_id': account_id}, {'$set': {'cart': account['cart']}})
    else:
        pass

    return {"result": result}
