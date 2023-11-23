import random
import time
import uuid
#from pymongo import MongoClient
from motor import motor_asyncio
from fastapi import FastAPI, Request
from pprint import pprint
from json import loads
from authorizenet import apicontractsv1
from authorizenet.apicontrollers import createTransactionController
from bson import ObjectId
import json
import yagmail
import re

with open("keys.json") as f:
    keys = json.load(f)

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

@app.post("/authorize")
async def authorize(request: Request):
    """
    Authorize a credit card (without actually charging it)
    """

    res = await request.body()
    # print(res)
    res = loads(res.decode())
    # print(res)
    session = await db.get_document('sessions', {'id': res['sessionId']})
    account_id = session['account']
    account = await db.get_document('accounts', {'_id': account_id})

    print(res)

    last_id = (await db.get_document('orders', {"type": "last_id"}))['id']
    new_id = last_id + random.randint(1, 13)

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

    # Check for cart
    if not account['cart']:
        return {"result": "missing cart"}

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
        original_item = await db.get_document('product-information', {'sku': item['sku']})
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
    line_item.unitPrice = '8.5'
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
                        "id": (await db.get_document("product-information", {"sku": i['sku']}))['_id'],
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
                            "country": res['items']['shipping']['country']
                        }
                    },
                    "items": items
                })

                await db.db['accounts'].update_one({'_id': account_id}, {'$set': {'cart': []}})
                await db.db['orders'].update_one({'type': 'last_id'}, {'$set': {'id': new_id}})

                email = yagmail.SMTP('thriveaudiollc@gmail.com', keys['gmail'])
                email.send(res['items']['shipping']['email'], f"DEV 775mv TEST Order #{new_id} confirmation", f"{res}")

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
        product = await db.get_document('product-information', {'_id': ObjectId(item['id'])})
        order['items'][i]['sku'] = product['sku']
        order['items'][i]['price'] = product['price']
    print(order)
    return order