my_dict = {
    'sku': 12345,
    'name': 23456,
    'desc': 2345
}

required_keys = ['name', 'sku', 'asdf']

for i in required_keys:
    if i in my_dict.keys():
        print('yay')
    else:
        print('no')