import asyncio
import datetime
import hashlib
import json
import random
import string
import time
from http.cookies import SimpleCookie
from urllib.parse import urlencode

from hoshino import aiorequests

from ..util import Dict, cache, get_config, get_next_day, gh_json, init_db
from .cookies import Genshin_Cookies

config = get_config()
config.use_cookie_index = 0
config.runtime = get_next_day()

db = init_db(config.cache_dir, 'uid.sqlite')
avatar_db = init_db(config.cache_dir, 'uid.sqlite', tablename='uid_avatars')

@cache(ttl=datetime.timedelta(hours=12))
async def character_list():
    return await gh_json('assets/character.json')

def get_db(qid):
    return db.get(qid, {})


def get_uid_by_qid(qid):
    return get_db(qid).get('uid')


def save_uid_by_qid(qid, uid):
    info = get_db(qid)
    info['uid'] = uid
    db[qid] = info


def get_cookie_by_qid(qid):
    return get_db(qid).get('cookie')


def save_cookie(qid, cookie):
    info = get_db(qid)
    info['cookie'] = cookie
    db[qid] = info


class Account_Error(Exception):
    def __init__(self, msg):
        self.msg = msg

    def __repr__(self):
        return self.msg

class LimitMessage(Exception):
    def __init__(self, use_count):
        self.use_count = use_count

    def __repr__(self):
        return f'''
公用({self.use_count}次)已经全部使用完,不是本插件限制,而是米游社限制
你可以使用yss来添加个人的使用次数
也可以私聊机器人 添加令牌? 来添加本群的使用次数
        '''.strip()

def __md5__(text):
    _md5 = hashlib.md5()
    _md5.update(text.encode())
    return _md5.hexdigest()


def __get_ds__(query, body=None):
    n = "xV8v4Qu54lUKrEYFZkJhB8cuOh9Asafs"
    i = str(int(time.time()))
    r = ''.join(random.sample(string.ascii_lowercase + string.digits, 6))
    q = '&'.join([f'{k}={v}' for k, v in query.items()])
    c = __md5__("salt=" + n + "&t=" + i + "&r=" + r + '&b=' + (body or '') +
                '&q=' + q)
    return i + "," + r + "," + c

cookie_info_cache = {}

async def get_cookie_info(cookie):
    account_id = SimpleCookie(cookie)['account_id'].value
    if cookie_info_cache.get(account_id):
        return cookie_info_cache[account_id]

    url = 'https://api-takumi-record.mihoyo.com/game_record/card/wapi/getGameRecordCard?uid=' + account_id

    headers = {
        'x-rpc-app_version': '2.16.1',
        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 13_2_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) miHoYoBBS/2.11.1',
        'x-rpc-client_type': '5',
        'x-rpc-device_id': ''.join(random.choices(string.ascii_lowercase + '1234567890', k=32)),
        'Cookie': cookie,
        'ds': __get_ds__({}, '')
    }
    res = await aiorequests.get(url=url, headers=headers, timeout=5)
    json_data = await res.json(object_hook=Dict)
    try:
        info = json_data.data.list[0]
    except Exception:
        info = {}
    cookie_info_cache[account_id] = info
    return cookie_info_cache[account_id]

last = {'current': 0, 'last': 0, 'all': 0}
group_use_index = {}


def get_global_cookies(index=0):
    cookies = config.setting.cookies
    cookies_len = len(cookies)
    if index >= cookies_len:
        return None, cookies_len
    else:
        return cookies[index], cookies_len


async def request_data(
    uid,
    api='index',
    character_ids=None,
    user_cookie=None,
    qid=None,
    group_id=None,
    force_user_cookie=None,
    no_log=False
):

    next_cookie = False
    now = datetime.datetime.now().timestamp()
    if now > config.runtime:
        config.runtime = get_next_day()
        config.use_cookie_index = 0
    server = 'cn_gf01'
    if uid[0] == "5":
        server = 'cn_qd01'

    cookie, cookies_len = get_global_cookies(config.use_cookie_index)
    all_can_use = cookies_len * 30
    group_cookies = False

    if not cookie:
        # 公用的使用完毕
        if not user_cookie and qid:
            user_cookie = get_cookie_by_qid(qid)

        # 优先使用群内设置的cookie
        group_cookies = Genshin_Cookies().db.get(group_id)
        if group_cookies:
            # 如果该群有设置了cookie 那么则使用该群的cookie 并且不使用个人的cookie
            group_index = group_use_index.get(group_id, {}).get('index', 0)
            if group_index == len(group_cookies):
                if user_cookie:
                    # 如果群内上限了 并且有个人的 那么使用个人的
                    cookie = user_cookie
                else:
                    group_limit_msg = f'\n本群 {group_index * 30} 次使用完毕'
                    raise Account_Error(repr(LimitMessage(all_can_use)) + group_limit_msg)
            else:
                # 如果群没限制 则使用群的cookie
                cookie = group_cookies[group_index]
                group_cookies = True

        elif user_cookie:
            # 如果群没设置过cookie 那么就使用自己的
            cookie = user_cookie

    if force_user_cookie:
        cookie = user_cookie or get_cookie_by_qid(qid)

    #mys限制不能看别人全部角色 那么如果查的是自己的 就直接使用已绑定yss的
    if not force_user_cookie:
        cookie_info = await get_cookie_info(cookie)
        if not cookie_info or cookie_info.game_role_id != uid:
            user_cookie = get_cookie_by_qid(qid)
            if user_cookie:
                cookie = user_cookie


    if not cookie:
        # 如果还是没 那么就提示上限
        raise LimitMessage(all_can_use)

    account_id = SimpleCookie(cookie)['account_id'].value
    if not no_log:
        print(
            '原神UID:(%s) 当前已查询%s次, 上一个账号查询%s次, 当前第%s个账号(%s), 一共%s个账号, 调用API-> %s' %
            (last['all'], last['current'] + 1, last['last'],
            config.use_cookie_index + 1, account_id, cookies_len, api))

    headers = {
        'Accept': 'application/json, text/plain, */*',
        "User-Agent":
        "Mozilla/5.0 (iPhone; CPU iPhone OS 13_2_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) miHoYoBBS/2.11.1",
        "Referer": "https://webstatic.mihoyo.com/",
        "x-rpc-app_version": "2.11.1",
        "x-rpc-client_type": '5',
        "DS": "",
        'Cookie': cookie
    }

    params = {"role_id": uid, "server": server}

    json_data = None
    fn = aiorequests.get
    base_url = 'https://api-takumi-record.mihoyo.com/game_record/app/genshin/api/%s'
    url = base_url % api + '?'
    if api == 'index':
        url += urlencode(params)
    elif api == 'spiralAbyss':
        params = {"role_id": uid, "schedule_type": 1, "server": server}
        url += urlencode(params)
    elif api == 'character':
        fn = aiorequests.post
        json_data = {"character_ids": character_ids}
        json_data.update(params)
        params = {}
    elif api == 'dailyNote':
        url += urlencode(params)

    headers['DS'] = __get_ds__(params, json_data and json.dumps(json_data))
    res = await fn(url=url, headers=headers, json=json_data)
    json_data = await res.json(object_hook=Dict)

    if json_data.retcode == 10104:
        raise Account_Error('UID[%s]信息获取失败, 请绑定正确的UID' % uid)

    if json_data.retcode == 10001:
        print('账号已失效 可能被修改密码, 请检查')
        next_cookie = True
    if json_data.retcode == 10103:
        print('error cookie [%s] (%s) !' %
              (config.use_cookie_index, account_id))
        next_cookie = True
    if json_data.retcode == 10101 or next_cookie:
        if user_cookie:
            print('user_cookie is limited!')
            raise Account_Error('个人使用的30次已上限~')
        print('cookie [%s] is limited!' % config.use_cookie_index)

        if group_cookies:
            group_use_index['group_id'] = dict(
                index=group_use_index.get(group_id, {}).get('index', 0) + 1)
        elif not user_cookie:
            config.use_cookie_index += 1
            last['last'] = last['current']
            last['current'] = 0
            if config.use_cookie_index == cookies_len:
                raise LimitMessage(all_can_use)

        return await request_data(
            uid=uid,
            api=api,
            character_ids=character_ids,
            user_cookie=user_cookie,
            qid=qid,
            group_id=group_id,
            force_user_cookie=force_user_cookie,
            no_log=no_log
        )

    last['current'] += 1
    last['all'] += 1

    if json_data.retcode != 0:
        raise Account_Error(f'{uid} 不存在,或者未在米游社公开.(请打开米游社,我的-个人主页-管理-公开信息)')

    return json_data

async def request_all_avatar(uid, raw_data, qid, group_id):
    return raw_data
    if raw_data.retcode == 0: #success
        avatar_number = raw_data.data.stats.avatar_number
        avatars_ids = set([str(x.id) for x in raw_data.data.avatars])
        if avatar_number != len(avatars_ids):
            # 如果显示不全人物
            print('uid: %s 获取全部角色信息' % uid)
            all_character = set((await character_list()).keys())
            # 查看数据库是否已经存储过
            avatar_db_ids = avatar_db.get(uid, [])
            if avatar_db_ids and avatar_number == len(avatar_db_ids):
                # 如果有 并且数量和现有角色对得上 那么返回缓存
                print('uid: %s 使用缓存' % uid)
                tasks = []
                for ids in [
                        avatar_db_ids[i:i + 8]
                        for i in range(0, len(avatar_db_ids), 8)
                ]:
                    tasks.append(character(uid, ids, qid, group_id))
                data = []
                futures = await asyncio.wait(tasks)
                for x in futures[0]:
                    try:
                        data.append(x.result().data.avatars)
                    except Exception as e:
                        if not isinstance(e, Account_Error):
                            print(repr(e))
                raw_data['data']['avatars'] = sum(data, [])
                return raw_data

            tasks = [
                character(uid, [int(x)], qid, group_id)
                for x in all_character - avatars_ids
            ]
            data = []
            futures = await asyncio.wait(tasks)
            for x in futures[0]:
                try:
                    data.append(*x.result().data.avatars)
                except Exception as e:
                    if not isinstance(e, Account_Error):
                        print(repr(e))
            print('uid: %s 获取完毕 一共 %s 个' % (uid, len(data)))
            raw_data['data']['avatars'] += data
            avatar_db[uid] = [x['id'] for x in raw_data['data']['avatars']]
    return raw_data


@cache(ttl=datetime.timedelta(minutes=30), arg_key='uid')
async def info(uid, qid=None, group_id=None):
    info_data = await request_data(uid, qid=qid, group_id=group_id)
    return await request_all_avatar(uid, info_data, qid, group_id)


@cache(ttl=datetime.timedelta(minutes=30), arg_key='uid')
async def spiralAbyss(uid, qid=None, group_id=None):
    return await request_data(uid, 'spiralAbyss', qid=qid, group_id=group_id)


# @cache(ttl=datetime.timedelta(minutes=30), arg_key='uid')
async def character(uid, character_ids, qid=None, group_id=None):
    return await request_data(uid,
                              'character',
                              character_ids,
                              qid=qid,
                              group_id=group_id,
                              no_log=True)


async def daily_note(cookie=None, qid=None):
    cookie_info = await get_cookie_info(cookie)
    if not cookie_info:
        raise Account_Error('绑定的cookie获取失败,请确保已绑定游戏账号')
    return await request_data(cookie_info.game_role_id,
                              'dailyNote',
                              user_cookie=cookie,
                              qid=qid,
                              force_user_cookie=True,
                              no_log=True)


class stats:
    def __init__(self, data, max_hide=False):
        self.data = data
        self.max_hide = max_hide

    @property
    def active_day(self) -> int:
        return self.data['active_day_number']

    @property
    def active_day_str(self) -> str:
        return '活跃天数: %s' % self.active_day

    @property
    def achievement(self) -> int:
        return self.data['achievement_number']

    @property
    def achievement_str(self) -> str:
        return '成就达成数: %s' % self.achievement

    @property
    def anemoculus(self) -> int:
        return self.data['anemoculus_number']

    @property
    def anemoculus_str(self) -> str:
        if self.max_hide and self.anemoculus == 66:
            return ''
        return '风神瞳: %s/66' % self.anemoculus

    @property
    def geoculus(self) -> int:
        return self.data['geoculus_number']

    @property
    def geoculus_str(self) -> str:
        if self.max_hide and self.geoculus == 131:
            return ''
        return '岩神瞳: %s/131' % self.geoculus

    @property
    def electroculus(self) -> int:
        return self.data['electroculus_number']

    @property
    def electroculus_str(self) -> str:
        if self.max_hide and self.electroculus == 95:
            return ''
        return '雷神瞳: %s/95' % self.electroculus

    @property
    def avatar(self) -> int:
        return self.data['avatar_number']

    @property
    def avatar_str(self) -> str:
        return '获得角色数: %s' % self.avatar

    @property
    def way_point(self) -> int:
        return self.data['way_point_number']

    @property
    def way_point_str(self) -> str:
        # if self.max_hide and self.way_point == 83:
        #     return ''
        return '解锁传送点: %s' % self.way_point

    @property
    def domain(self) -> int:
        return self.data['domain_number']

    @property
    def domain_str(self) -> str:
        return '解锁秘境: %s' % self.domain

    @property
    def spiral_abyss(self) -> str:
        return self.data['spiral_abyss']

    @property
    def spiral_abyss_str(self) -> str:
        return '' if self.spiral_abyss == '-' else '当期深境螺旋: %s' % self.spiral_abyss

    @property
    def common_chest(self) -> int:
        return self.data['common_chest_number']

    @property
    def common_chest_str(self) -> str:
        return '普通宝箱: %s' % self.common_chest

    @property
    def exquisite_chest(self) -> int:
        return self.data['exquisite_chest_number']

    @property
    def exquisite_chest_str(self) -> str:
        return '精致宝箱: %s' % self.exquisite_chest

    @property
    def luxurious_chest(self) -> int:
        return self.data['luxurious_chest_number']

    @property
    def luxurious_chest_str(self) -> str:
        return '华丽宝箱: %s' % self.luxurious_chest

    @property
    def precious_chest(self) -> int:
        return self.data['precious_chest_number']

    @property
    def precious_chest_str(self) -> str:
        return '珍贵宝箱: %s' % self.precious_chest

    @property
    def string(self):
        str_list = [
            self.active_day_str, self.achievement_str, self.anemoculus_str,
            self.geoculus_str, self.electroculus_str, self.avatar_str,
            self.way_point_str, self.domain_str, self.spiral_abyss_str,
            self.luxurious_chest_str, self.precious_chest_str,
            self.exquisite_chest_str, self.common_chest_str
        ]
        return '\n'.join(list(filter(None, str_list)))
