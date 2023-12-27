import random
import time
import uuid
#from pymongo import MongoClient
from motor import motor_asyncio
from fastapi import FastAPI, Request
from fastapi_utils.tasks import repeat_every
from pprint import pprint
from json import loads
from authorizenet import apicontractsv1
from authorizenet.apicontrollers import createTransactionController
from bson import ObjectId
import json
import yagmail
import re
from jinja2 import Environment, FileSystemLoader
import bcrypt

app = FastAPI()

class SiteDB:
    def __init__(self):
        #self.db = MongoClient('localhost', 27017)['775mv_dev']
        self.db = motor_asyncio.AsyncIOMotorClient('localhost', 27017)['775mv_dev']

    async def get_collection_as_list(self, collection: str):
        documents = []
        db_collection = self.db[collection]
        async for i in db_collection.find():
            i['_id'] = str(i['_id'])
            documents.append(i)
        return documents

    async def get_document(self, collection: str, document: dict):
        documents = self.db[collection]
        doc = await documents.find_one(document)
        if not doc:
            return None
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
    #print(db.get_collection_as_list('products'))
    required_fields = ['_id', 'sku', 'name', 'price', 'description']
    checked_docs = []
    for i in await db.get_collection_as_list('products'):
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
    return checked_docs#db.get_collection_as_list('products')
    #return {'products': [{'name': 'filter', 'price': 20}, {'name': 'filter2', 'price': 10}]}


@app.get("/get-product/{sku}")
async def product(sku: str):
    print(sku)
    doc = await db.get_document('products', {'sku': sku})
    with open(f'static/{sku}/desc.md') as f:
        doc['desc'] = f.read()
    with open(f'static/{sku}/specs.md') as f:
        doc['specs'] = f.read()
    with open(f'static/{sku}/short_desc.md') as f:
        doc['description'] = f.read()

    return doc


@app.get("/session-id")
async def new_session_id():
    doc = await db.post_document('accounts', {
        "new_emails": {},
        "email": "",
        "old_emails": [],
        "password": "",
        "salt": "",
        "timer var": 0,
        "timer": 0,
        "cart": [],
        "orders": []
    })

    uid = str(uuid.uuid4())
    config = await db.get_document("config", {'type': 'config'})

    await db.post_document('sessions', {
        "id": uid,
        "account": doc.inserted_id,
        "state": "unknown",
        "expiration": time.time()+config['short_session'],
        "trusted_device": False
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
    res = loads(res.decode())
    if "sessionId" not in res.keys():
        return []
    session = await db.get_document('sessions', {'id': res['sessionId']})
    account_id = session['account']
    account = await db.get_document('accounts', {'_id': account_id})
    print(account)

    for item in account['cart']:
        db_item = await db.get_document('products', {'sku': item['sku']})
        item['price'] = db_item['price']
        item['name'] = db_item['name']
        item['description'] = db_item['description']

    return account['cart']

@app.post("/update-cart")
async def update_cart(request: Request):
    result = "ok"
    res = await request.body()
    res = loads(res.decode())
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

@app.post("/authorize")
async def authorize(request: Request):
    """
    Authorize a credit card (without actually charging it)
    """

    res = await request.body()
    res = loads(res.decode())
    session = await db.get_document('sessions', {'id': res['sessionId']})
    account_id = session['account']
    account = await db.get_document('accounts', {'_id': account_id})
    config = await db.get_document("config", {'type': 'config'})

    print(res)

    last_id = (await db.get_document('orders', {"type": "last_id"}))['id']
    new_id = last_id + random.randint(1, 13)

    # Check for cart
    if not account['cart']:
        return {"result": "missing cart"}

    # Check for shipping information
    for item in res['items']:
        if item != ['expanded']:
            print(res["items"]["billing"]["same_as_shipping"])
            if item == "billing" and res["items"]["billing"]["same_as_shipping"]:
                print('GOT HERE')
                continue
            for i in res['items'][item].keys():
                if res['items'][item][i] == "" and i != "address2":
                    return {"result": f"missing {item} {i}"}

    # Create a merchantAuthenticationType object with authentication details
    # retrieved from the constants file
    merchantAuth = apicontractsv1.merchantAuthenticationType()
    merchantAuth.name = "34UTh2qF6d"
    merchantAuth.transactionKey = "49F877p4KvPBUgwR"

    # Create the payment data for a credit card
    creditCard = apicontractsv1.creditCardType()
    creditCard.cardNumber = "4111111111111111"
    creditCard.expirationDate = "2035-12"
    creditCard.cardCode = "123"

    # Add the payment data to a paymentType object
    payment = apicontractsv1.paymentType()
    payment.creditCard = creditCard

    # Create order information
    order = apicontractsv1.orderType()
    order.invoiceNumber = str(new_id)

    # Set the customer's Bill To address
    customerAddress = apicontractsv1.customerAddressType()
    customerAddress.firstName = res['items']['shipping']['first_name']
    customerAddress.lastName = res['items']['shipping']['last_name']
    if res['items']['billing']['same_as_shipping']:
        customerAddress.address = res['items']['shipping']['address1'] + f'\n{res["items"]["shipping"]["address2"]}' if res['items']["shipping"]["address2"] != '' else ''
        customerAddress.city = res['items']['shipping']['city']
        customerAddress.state = res['items']['shipping']['state']
        customerAddress.zip = res['items']['shipping']['zip']
        customerAddress.country = res['items']['shipping']['country']
    else:
        customerAddress.address = res['items']['billing']['address'] + f'\n{res["items"]["billing"]["address2"]}' if res['items']["billing"]["address2"] != '' else ''
        customerAddress.city = res['items']['billing']['city']
        customerAddress.state = res['items']['billing']['state']
        customerAddress.zip = res['items']['billing']['zip']
        customerAddress.country = res['items']['billing']['country']

    # Set the customer's identifying information
    # customerData = apicontractsv1.customerDataType()
    # customerData.type = "individual"
    # customerData.id = "99999456654"
    # customerData.email = "EllenJohnson@example.com"

    # Add values for transaction settings
    duplicateWindowSetting = apicontractsv1.settingType()
    duplicateWindowSetting.settingName = "duplicateWindow"
    duplicateWindowSetting.settingValue = "1" # 600
    settings = apicontractsv1.ArrayOfSetting()
    settings.setting.append(duplicateWindowSetting)

    # setup individual line items & build the array of line items
    line_items = apicontractsv1.ArrayOfLineItem()
    total_price = 0
    for item in account['cart']:
        original_item = await db.get_document('products', {'sku': item['sku']})
        total_price += int(item['amount']) * original_item['price']

        line_item = apicontractsv1.lineItemType()
        line_item.itemId = item['sku']
        line_item.name = item['sku']
        line_item.description = original_item['description']
        line_item.quantity = item['amount']
        line_item.unitPrice = original_item['price']
        line_items.lineItem.append(line_item)

    line_item = apicontractsv1.lineItemType()
    line_item.itemId = 'shipping'
    line_item.name = 'Shipping price'
    line_item.description = 'The shipping cost'
    line_item.quantity = '1'

    shipping_added = False
    shipping_price = 0
    for country in config['shipping_price'].keys():
        if country == res['items']['shipping']['country']:
            line_item.unitPrice = str(config['shipping_price'][country])
            total_price += config['shipping_price'][country]
            shipping_price = config['shipping_price'][country]
            shipping_added = True
    if not shipping_added:
        line_item.unitPrice = str(config['shipping_price']["Worldwide"])
        total_price += config['shipping_price']["Worldwide"]
        shipping_price = config['shipping_price']["Worldwide"]

    line_items.lineItem.append(line_item)

    # Create a transactionRequestType object and add the previous objects to it.
    transactionrequest = apicontractsv1.transactionRequestType()
    transactionrequest.transactionType = "authOnlyTransaction"
    transactionrequest.amount = total_price # good
    transactionrequest.payment = payment # good
    transactionrequest.order = order # good* *need order id (scroll up)
    transactionrequest.billTo = customerAddress # good
    transactionrequest.transactionSettings = settings # good
    transactionrequest.lineItems = line_items # good

    # Assemble the complete transaction request
    createtransactionrequest = apicontractsv1.createTransactionRequest()
    createtransactionrequest.merchantAuthentication = merchantAuth # good
    createtransactionrequest.refId = "MerchantID-0001"
    createtransactionrequest.transactionRequest = transactionrequest # good
    # Create the controller
    createtransactioncontroller = createTransactionController(
        createtransactionrequest)
    createtransactioncontroller.execute()

    response = createtransactioncontroller.getresponse()

    if response is not None:
        # Check to see if the API request was successfully received and acted upon
        if response.messages.resultCode == "Ok":
            # Since the API request was successful, look for a transaction response
            # and parse it to display the results of authorizing the card
            if hasattr(response.transactionResponse, 'messages') is True:
                print(
                    'Successfully created transaction with Transaction ID: %s'
                    % response.transactionResponse.transId)
                print('Transaction Response Code: %s' %
                      response.transactionResponse.responseCode)
                print('Message Code: %s' %
                      response.transactionResponse.messages.message[0].code)
                print('Description: %s' % response.transactionResponse.
                      messages.message[0].description)

                items = []
                for i in account['cart']:
                    items.append({
                        "id": (await db.get_document("products", {"sku": i['sku']}))['_id'],
                        "amount": i['amount']
                    })
                order_id = await db.post_document("orders", {
                    "id": new_id,
                    "time": {
                      "ordered": time.time(),
                      "shipped": 0,
                      "delivered": 0
                    },
                    "payment_status": "authorized",
                    "payment_method": "card",
                    "authorize_id": str(response.transactionResponse.transId),
                    "order_status": "processing",
                    "user": {
                        "account": account['_id'],
                        "contact": {
                            "first_name": res['items']['shipping']['first_name'],
                            "last_name": res['items']['shipping']['last_name'],
                            "email": res['items']['shipping']['email'],
                        },
                        "shipping": {
                            "address1": res['items']['shipping']['address1'],
                            "address2": res['items']['shipping']['address2'],
                            "city": res['items']['shipping']['city'],
                            "state": res['items']['shipping']['state'],
                            "zip": res['items']['shipping']['zip'],
                            "country": res['items']['shipping']['country'],
                            "price": shipping_price
                        }
                    },
                    "items": items
                })

                account['orders'].append(ObjectId(order_id.inserted_id))
                await db.db['accounts'].update_one({'_id': account_id}, {'$set': {'cart': []}})
                # await db.db['accounts'].update_one({'_id': account_id}, {'$set': {'email': res['items']['shipping']['email']}})
                await db.db['accounts'].update_one({'_id': account_id}, {'$set': {'orders': account['orders']}})
                await db.db['orders'].update_one({'type': 'last_id'}, {'$set': {'id': new_id}})
                config = await db.get_document("config", {'type': 'config'})

                email = yagmail.SMTP('thriveaudiollc@gmail.com', config['gmail'])

                # env = Environment(loader=FileSystemLoader('email_templates'))
                # email.send(res['email']['shipping']['email'], f"DEV 775mv TEST Order #{new_id} confirmation",env.get_template('email-confirmation.html').render(user=res, items=items))

                total = 0
                amount = 0
                for item in account['cart']:
                    db_item = await db.get_document('products', {'sku': item['sku']})
                    item['price'] = db_item['price']
                    item['name'] = db_item['name']
                    amount += item['amount']
                    total += item['price'] * item['amount']
                total += shipping_price

                res['items']['shipping']['price'] = shipping_price
                res['items']['total'] = total
                res['items']['amount'] = amount

                # email.send(res['items']['shipping']['email'], f"DEV 775mv TEST Order #{new_id} confirmation", f"{res} {account['cart']}")
                env = Environment(loader=FileSystemLoader('email_templates'))

                email.send(res['items']['shipping']['email'], f"DEV 775mv TEST Order #{new_id} confirmation",env.get_template('order-confirmation.html').render(user=res['items'], items=account['cart'], id=order_id.inserted_id))
                email.send("thriveaudiollc@gmail.com", f"TEST New Order #{new_id} | {res['items']['shipping']['first_name']} {res['items']['shipping']['last_name']}", env.get_template('new-order.html').render(user=res['items'], items=account['cart']))

                return {"result" : f"success {order_id.inserted_id}"}
            else:
                print('Failed Transaction.')
                if hasattr(response.transactionResponse, 'errors') is True:
                    print('Error Code:  %s' % str(response.transactionResponse.
                                                  errors.error[0].errorCode))
                    print(
                        'Error message: %s' %
                        response.transactionResponse.errors.error[0].errorText)
                    return {"result": "error "+response.transactionResponse.errors.error[0].errorText}
        # Or, print errors if the API request wasn't successful
        else:
            print('Failed Transaction.')
            if hasattr(response, 'transactionResponse') is True and hasattr(
                    response.transactionResponse, 'errors') is True:
                print('Error Code: %s' % str(
                    response.transactionResponse.errors.error[0].errorCode))
                print('Error message: %s' %
                      response.transactionResponse.errors.error[0].errorText)
                return {"result": "error "+response.transactionResponse.errors.error[0].errorText}
            else:
                print('Error Code: %s' %
                      response.messages.message[0]['code'].text)
                print('Error message: %s' %
                      response.messages.message[0]['text'].text)
                return {"result": "error " + response.messages.message[0]['text'].text}
    else:
        print('Null Response.')
        return {"result": "error null response"}

    return {"result": "error unknown"}

@app.post("/order/{id}")
async def get_order(request: Request, id: str):
    order = await db.get_document('orders', {'_id': ObjectId(id)})
    for i, item in enumerate(order['items']):
        product = await db.get_document('products', {'_id': ObjectId(item['id'])})
        order['items'][i]['sku'] = product['sku']
        order['items'][i]['price'] = product['price']
    print(order)
    return order

@app.post("/check-loggedin")
async def check_loggedin(request: Request):
    res = await request.body()
    res = loads(res.decode())
    session = await db.get_document('sessions', {'id': res['sessionId']})
    return {"result": session['state'] == "loggedin"}

def hashh(password: str, salt: str):
    password = password.encode('utf-8')
    salt = salt.encode('utf-8')
    return str(bcrypt.hashpw(password, salt), encoding="utf-8") # TODO hash password

@app.post("/register")
async def register(request: Request):
    res = await request.body()
    res = loads(res.decode())
    session = await db.get_document('sessions', {'id': res['sessionId']})
    account_id = session['account']
    account = await db.get_document('accounts', {'_id': account_id})
    config = await db.get_document("config", {'type': 'config'})
    session_length = 0
    if res['items']['check']:
        session_length = config['long_session']
    else:
        session_length = config['short_session']

    print(res)

    if session['state'] == "registered" or session['state'] == "loggedin":
        hashed = hashh(res['items']['password'], account['salt'])
        if account['password'] == hashed:
            # await db.db['accounts'].update_one({'_id': account_id}, {'$set': {'password': hashed}})
            await db.db['sessions'].update_one({'id': res['sessionId']}, {'$set': {'state': "loggedin"}})
            await db.db['sessions'].update_one({'id': res['sessionId']}, {'$set': {'expiration': time.time() + session_length}})
            await db.db['sessions'].update_one({'id': res['sessionId']}, {'$set': {'trusted_device': res['items']['check']}})
            return {"result": "redirect"}
        else:
            return {"result": "error"}
    else:
        if validate_password(res['items']['password']):
            salt = str(bcrypt.gensalt(), encoding="utf-8")
            await db.db['accounts'].update_one({'_id': account_id}, {'$set': {'salt': salt}})
            hashed = hashh(res['items']['password'], salt)
            # await db.db['accounts'].update_one({'_id': account_id}, {'$set': {'email': res['items']['email']}})
            await db.db['accounts'].update_one({'_id': account_id}, {'$set': {'password': hashed}})
            await db.db['sessions'].update_one({'id': res['sessionId']}, {'$set': {'state': "loggedin"}})
            await db.db['sessions'].update_one({'id': res['sessionId']}, {'$set': {'expiration': time.time() + session_length}})
            await db.db['sessions'].update_one({'id': res['sessionId']}, {'$set': {'trusted_device': res['items']['check']}})
            return {"result": "redirect"}
        else:
            return {"result": "password"}

@app.post("/login")
async def login(request: Request):
    res = await request.body()
    res = loads(res.decode())
    print(res)
    session = await db.get_document('sessions', {'id': res['sessionId']})
    # account_id = session['account']
    #account = await db.get_document('accounts', {'_id': account_id})
    account = await db.get_document('accounts', {'email': res['items']['email']})
    config = await db.get_document("config", {'type': 'config'})
    session_length = 0
    if res['items']['check']:
        session_length = config['long_session']
    else:
        session_length = config['short_session']

    # Check if account exists
    if not account:
        return {"result": "error -1"}
    else:
        await db.db['sessions'].update_one({'id': res['sessionId']}, {'$set': {'account': ObjectId(account['_id'])}})
        hashed = hashh(res['items']['password'], account['salt'])
        if account['password'] == hashed:
            # passwords match
            await db.db['accounts'].update_one({'_id': ObjectId(account['_id'])}, {'$set': {'timer var': 0}})
            await db.db['sessions'].update_one({'id': res['sessionId']}, {'$set': {'state': 'loggedin'}})
            await db.db['sessions'].update_one({'id': res['sessionId']}, {'$set': {'expiration': time.time()+session_length}})
            await db.db['sessions'].update_one({'id': res['sessionId']}, {'$set': {'trusted_device': res['items']['check']}})
            return {"result": "redirect"}
        else:
            # passwords don't match
            if account['timer var'] == 0:
                # timer var is zero
                print("GOT HERE")
                print(account['timer var'])
                await db.db['accounts'].update_one({'_id': ObjectId(account['_id'])}, {'$set': {'timer var': account['timer var']+5}})
                await db.db['accounts'].update_one({'_id': ObjectId(account['_id'])}, {'$set': {'timer': time.time() + account['timer var']+5}})
                return {"result": f"error {time.time() + account['timer var']+5+1}"}
            else:
                # timer var is more than zero
                if account['timer'] < time.time():
                    # timer expired
                    await db.db['accounts'].update_one({'_id': ObjectId(account['_id'])}, {'$set': {'timer var': account['timer var'] + 5}})
                    await db.db['accounts'].update_one({'_id': ObjectId(account['_id'])}, {'$set': {'timer': time.time() + account['timer var'] + 5}})
                    return {"result": f"error {time.time() + account['timer var'] + 5+1}"}
                else:
                    # timer still ticking
                    await db.db['accounts'].update_one({'_id': ObjectId(account['_id'])}, {'$set': {'timer var': account['timer var'] * 2}})
                    await db.db['accounts'].update_one({'_id': ObjectId(account['_id'])}, {'$set': {'timer': account['timer'] + account['timer var'] * 2}})
                    return {"result": f"error {account['timer'] + account['timer var'] * 2+1}"}

@app.post("/logout")
async def logout(request: Request):
    res = await request.body()
    res = loads(res.decode())
    session = await db.get_document('sessions', {'id': res['sessionId']})

    if session['trusted_device']:
        await db.db['sessions'].update_one({'id': res['sessionId']}, {'$set': {'state': "registered"}})
    else:
        await db.db['sessions'].update_one({'_id': ObjectId(session['_id'])}, {'$set': {'state': 'unknown'}})
        doc = await db.post_document('accounts', {
            "new_emails": {},
            "email": "",
            "old_emails": [],
            "password": "",
            "timer var": 0,
            "timer": 0,
            "cart": [],
            "orders": []
        })
        await db.db['sessions'].update_one({'_id': ObjectId(session['_id'])}, {'$set': {'account': doc.inserted_id}})

    return {'result': "redirect"}

@app.post("/settings")
async def settings(request: Request):
    res = await request.body()
    res = loads(res.decode())
    session = await db.get_document('sessions', {'id': res['sessionId']})
    account_id = session['account']
    account = await db.get_document('accounts', {'_id': account_id})

    response = {"result": "success"}
    response['email'] = account['email']
    # response['email confirmed'] = account['email confirmed']
    response['password'] = ""

    if session['state'] == "loggedin":
        return response
    else:
        return {"result": "error"}

def validate_email(email):
    return re.match(".+@.+\..+", email)

def validate_password(password):
    if not re.search("[0-9]+", password):
        return False
    if not re.search("[a-z]+", password):
        return False
    if not re.search("[A-Z]+", password):
        return False
    if not re.search("[^a-zA-Z0-9 \n]+", password):
        return False
    return True

@app.post("/update-password")
async def update_password(request: Request):
    res = await request.body()
    res = loads(res.decode())
    session = await db.get_document('sessions', {'id': res['sessionId']})
    account_id = session['account']
    account = await db.get_document('accounts', {'_id': account_id})
    print(res)

    if res['items']['password'] != "":
        if validate_password(res['items']['password']):
            await db.db['accounts'].update_one({'_id': account_id}, {'$set': {'password': hashh(res['items']['password'], account['salt'])}})
            return {"result": "success"}
        else:
            return {"result": "error"}

@app.post("/orders")
async def orders(request: Request):
    res = await request.body()
    res = loads(res.decode())
    session = await db.get_document('sessions', {'id': res['sessionId']})

    if session['state'] == "loggedin":

        account_id = session['account']
        account = await db.get_document('accounts', {'_id': account_id})

        order_list = {"result": "success", "items": []}

        for i in account['orders']:
            order = {}
            db_order = await db.get_document('orders', {'_id': i})
            order['id'] = db_order['id']
            order['db_id'] = db_order['_id']
            order['order_status'] = db_order['order_status']
            items = 0
            total = 0
            for item in db_order['items']:
                product = await db.get_document('products', {'_id': ObjectId(item['id'])})
                items += item['amount']
                total += item['amount'] * product['price']

            config = await db.get_document("config", {'type': 'config'})

            shipping_added = False
            for i, x in enumerate(config['shipping_price'].keys()):
                if db_order['user']['shipping']['country'] == x:
                    total += config['shipping_price'][db_order['user']['shipping']['country']]
                    shipping_added = True
            if not shipping_added:
                total += config['shipping_price']["Worldwide"]

            order['items'] = items
            order['total'] = total
            order_list['items'].append(order)

        return order_list
    else:
        return {"result": "error"}

@app.post("/confirm-email")
async def confirm_email(request: Request):
    # new UUID
    # record new email
    # send confirmation email
    res = await request.body()
    res = loads(res.decode())
    if not validate_email(res['email']):
        return {"result": "error"}

    session = await db.get_document('sessions', {'id': res['sessionId']})
    account_id = session['account']
    account = await db.get_document('accounts', {'_id': account_id})

    if account['email'] == res['email']:
        return {"result": "confirmed"}

    for i, email in enumerate(account['old_emails']):
        if email == res['email']:
            account['old_emails'].append(account['email'])
            account['email'] = email
            account['old_emails'].pop(i)

            await db.db['accounts'].update_one({'_id': account_id}, {'$set': {'email': account['email']}})
            await db.db['accounts'].update_one({'_id': account_id}, {'$set': {'old_emails': account['old_emails']}})

            return {"result": "confirmed"}

    uid = str(uuid.uuid4())
    account['new_emails'][uid] = res['email']
    await db.db['accounts'].update_one({'_id': account_id}, {'$set': {'new_emails': account['new_emails']}})

    config = await db.get_document("config", {'type': 'config'})
    email = yagmail.SMTP('thriveaudiollc@gmail.com', config['gmail'])
    env = Environment(loader= FileSystemLoader('email_templates'))
    email.send(res['email'], "775mv Email Confirmation", env.get_template('email-confirmation.html').render(id=uid))
    return {"result": "success"}


@app.post("/email-confirmed")
async def email_confirmed(request: Request):
    res = await request.body()
    res = loads(res.decode())
    print(res)
    session = await db.get_document('sessions', {'id': res['sessionId']})
    account_id = session['account']
    account = await db.get_document('accounts', {'_id': account_id})

    if account['email'] == res['email']:
        return {"result": True}

    for email in account['old_emails']:
        if email == res['email']:
            return {"result": True}

    return {'result': False}

@app.post("/check-email-id/{id}")
async def check_email_id(request: Request, id: str):
    res = await request.body()
    res = loads(res.decode())
    print(res, id)
    # session = await db.get_document('sessions', {'id': res['sessionId']})
    # account_id = session['account']
    # account = await db.get_document('accounts', {'_id': account_id})
    accounts = await db.get_collection_as_list('accounts')
    for account in accounts:
        for i in account['new_emails'].keys():
            if i == id:
                if account['email'] != "":
                    account['old_emails'].append(account['email'])
                account['email'] = account['new_emails'][i]
                account['new_emails'].pop(i)
                await db.db['accounts'].update_one({'_id': ObjectId(account['_id'])}, {'$set': {'email': account['email']}})
                await db.db['accounts'].update_one({'_id': ObjectId(account['_id'])}, {'$set': {'old_emails': account['old_emails']}})
                await db.db['accounts'].update_one({'_id': ObjectId(account['_id'])}, {'$set': {'new_emails': account['new_emails']}})
                return {"result": "success"}
    # for i in account['email_ids']:
    #     if i == id:
    #         return {"result": "success"}

    return {"result": "error"}

@app.post("/get-shipping-methods")
async def get_shipping_methods(request: Request):
    config = await db.get_document("config", {'type': 'config'})
    return config['shipping_price']

@app.post("/keep-alive")
async def keep_alive(request: Request):
    res = await request.body()
    res = loads(res.decode())
    print(res)
    session = await db.get_document('sessions', {'id': res['sessionId']})
    if not session['trusted_device']:
        if time.time() < session['expiration']:
            config = await db.get_document("config", {'type': 'config'})
            await db.db['sessions'].update_one({'id': res['sessionId']}, {'$set': {'expiration': time.time()+config['short_session']}})

@app.on_event("startup")
@repeat_every(seconds=60)
async def logout_expired_sessions():
    sessions = await db.get_collection_as_list('sessions')
    for session in sessions:
        if time.time() > session['expiration'] and session['state'] != "unknown":
            if not session['trusted_device']:
                await db.db['sessions'].update_one({'_id': ObjectId(session['_id'])}, {'$set': {'state': 'unknown'}})
                doc = await db.post_document('accounts', {
                    "new_emails": {},
                    "email": "",
                    "old_emails": [],
                    "password": "",
                    "timer var": 0,
                    "timer": 0,
                    "cart": [],
                    "orders": []
                })
                await db.db['sessions'].update_one({'_id': ObjectId(session['_id'])}, {'$set': {'account': doc.inserted_id}})
            else:
                await db.db['sessions'].update_one({'_id': ObjectId(session['_id'])}, {'$set': {'state': 'registered'}})

@app.post("/trusted-check")
async def trusted_check(request: Request):
    res = await request.body()
    res = loads(res.decode())
    print(res)
    session = await db.get_document('sessions', {'id': res['sessionId']})
    print(session['trusted_device'])
    return session['trusted_device']