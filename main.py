import aiohttp
import asyncio
from api.qinglong import QlApi, QlOpenApi
from api.send import SendApi
from config import (
    auto_move,
    qinglong_data,
    user_datas,
    auto_shape_recognition,
)
import cv2
import json
from loguru import logger
import os
from playwright.async_api import Playwright, async_playwright
import random
import re
from PIL import Image  # 用于图像处理
import traceback
from typing import Union
from utils.consts import (
    jd_login_url,
    supported_types,
    supported_colors,
    supported_sms_func
)
from utils.tools import (
    get_tmp_dir,
    get_img_bytes,
    get_forbidden_users_dict,
    filter_forbidden_users,
    save_img,
    get_ocr,
    get_word,
    get_shape_location_by_type,
    get_shape_location_by_color,
    rgba2rgb,
    send_msg,
    new_solve_slider_captcha,
    ddddocr_find_files_pic,
    expand_coordinates,
    cv2_save_img,
    ddddocr_find_bytes_pic,
    solve_slider_captcha
)

"""
基于playwright做的
"""
logger.add(
    sink="main.log",
    level="DEBUG"
)


async def download_image(url, filepath):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                with open(filepath, 'wb') as f:
                    f.write(await response.read())
                print(f"Image downloaded to {filepath}")
            else:
                print(f"Failed to download image. Status code: {response.status}")


async def auto_move_slide(page, retry_times: int = 2, slider_selector: str = 'img.move-img', move_solve_type: str = ""):
    """
    自动识别移动滑块验证码
    """
    from config import slide_difference
    for i in range(retry_times):
        logger.info(f'第{i + 1}次尝试自动移动滑块中...')
        try:
            # 查找小图
            await page.wait_for_selector('#small_img', state='visible', timeout=3000)
        except Exception as e:
            # 未找到元素，认为成功，退出循环
            logger.info('未找到小图,退出移动滑块')
            break

        # 获取 src 属性
        small_src = await page.locator('#small_img').get_attribute('src')
        background_src = await page.locator('#cpc_img').get_attribute('src')

        # 获取 bytes
        small_img_bytes = get_img_bytes(small_src)
        background_img_bytes = get_img_bytes(background_src)

        # 保存小图
        small_img_path = save_img('small_img', small_img_bytes)
        small_img_width = await page.evaluate('() => { return document.getElementById("small_img").clientWidth; }')  # 获取网页的图片尺寸
        small_img_height = await page.evaluate('() => { return document.getElementById("small_img").clientHeight; }')  # 获取网页的图片尺寸
        small_image = Image.open(small_img_path)  # 打开图像
        resized_small_image = small_image.resize((small_img_width, small_img_height))  # 调整图像尺寸
        resized_small_image.save(small_img_path)  # 保存调整后的图像

        # 保存大图
        background_img_path = save_img('background_img', background_img_bytes)
        background_img_width = await page.evaluate('() => { return document.getElementById("cpc_img").clientWidth; }')  # 获取网页的图片尺寸
        background_img_height = await page.evaluate('() => { return document.getElementById("cpc_img").clientHeight; }')  # 获取网页的图片尺寸
        background_image = Image.open(background_img_path)  # 打开图像
        resized_background_image = background_image.resize((background_img_width, background_img_height))  # 调整图像尺寸
        resized_background_image.save(background_img_path)  # 保存调整后的图像

        # 获取滑块
        slider = page.locator(slider_selector)
        await asyncio.sleep(1)

        if move_solve_type == "old":
            # 用于调试
            distance = ddddocr_find_bytes_pic(small_img_bytes, background_img_bytes)
            await asyncio.sleep(1)
            await solve_slider_captcha(page, slider, distance, slide_difference)
            await asyncio.sleep(1)
            continue
        # 获取要移动的长度
        distance = ddddocr_find_files_pic(small_img_path, background_img_path)
        await asyncio.sleep(1)
        # 移动滑块
        await new_solve_slider_captcha(page, slider, distance, slide_difference)
        await asyncio.sleep(1)


async def auto_shape(page, retry_times: int = 5):
    # 图像识别
    ocr = get_ocr(beta=True)
    # 文字识别
    det = get_ocr(det=True)
    """
    自动识别滑块验证码
    """
    for i in range(retry_times):
        logger.info(f'第{i + 1}次自动识别形状中...')
        try:
            # 查找小图
            await page.wait_for_selector('div.captcha_footer img', state='visible', timeout=3000)
        except Exception as e:
            # 未找到元素，认为成功，退出循环
            logger.info('未找到形状图,退出识别状态')
            break

        tmp_dir = get_tmp_dir()

        background_img_path = os.path.join(tmp_dir, f'background_img.png')
        # 获取大图元素
        background_locator = page.locator('#cpc_img')
        # 获取元素的位置和尺寸
        backend_bounding_box = await background_locator.bounding_box()
        backend_top_left_x = backend_bounding_box['x']
        backend_top_left_y = backend_bounding_box['y']

        # 截取元素区域
        await page.screenshot(path=background_img_path, clip=backend_bounding_box)

        # 获取 图片的src 属性和button按键
        word_img_src = await page.locator('div.captcha_footer img').get_attribute('src')
        button = page.locator('div.captcha_footer button.sure_btn')

        # 找到刷新按钮
        refresh_button = page.locator('.jcap_refresh')


        # 获取文字图并保存
        word_img_bytes = get_img_bytes(word_img_src)
        rgba_word_img_path = save_img('rgba_word_img', word_img_bytes)

        # 文字图是RGBA的，有蒙板识别不了，需要转成RGB
        rgb_word_img_path = rgba2rgb('rgb_word_img', rgba_word_img_path)

        # 获取问题的文字
        word = get_word(ocr, rgb_word_img_path)

        if word.find('色') > 0:
            target_color = word.split('请选出图中')[1].split('的图形')[0]
            if target_color in supported_colors:
                logger.info(f'正在点击中......')
                # 获取点的中心点
                center_x, center_y = get_shape_location_by_color(background_img_path, target_color)
                if center_x is None and center_y is None:
                    logger.info(f'识别失败,刷新中......')
                    await refresh_button.click()
                    await asyncio.sleep(random.uniform(2, 4))
                    continue
                # 得到网页上的中心点
                x, y = backend_top_left_x + center_x, backend_top_left_y + center_y
                # 点击图片
                await page.mouse.click(x, y)
                await asyncio.sleep(random.uniform(1, 4))
                # 点击确定
                await button.click()
                await asyncio.sleep(random.uniform(2, 4))
                continue
            else:
                logger.info(f'不支持{target_color},刷新中......')
                # 刷新
                await refresh_button.click()
                await asyncio.sleep(random.uniform(2, 4))
                continue

        # 这里是文字验证码了
        elif word.find('依次') > 0:
            logger.info(f'开始文字识别,点击中......')
            # 获取文字的顺序列表
            target_char_list = list(re.findall(r'[\u4e00-\u9fff]+', word)[1])
            target_char_len = len(target_char_list)

            # 识别字数不对
            if target_char_len != 4:
                logger.info(f'识别的字数不对,刷新中......')
                await refresh_button.click()
                await asyncio.sleep(random.uniform(2, 4))
                continue

            # 定义【文字, 坐标】的列表
            target_list = [[x, []] for x in target_char_list]

            # 获取大图的二进制
            background_locator = page.locator('#cpc_img')
            background_locator_src = await background_locator.get_attribute('src')
            background_locator_bytes = get_img_bytes(background_locator_src)
            bboxes = det.detection(background_locator_bytes)

            count = 0
            im = cv2.imread(background_img_path)
            for bbox in bboxes:
                # 左上角
                x1, y1, x2, y2 = bbox
                # 做了一下扩大
                expanded_x1, expanded_y1, expanded_x2, expanded_y2 = expand_coordinates(x1, y1, x2, y2, 10)
                im2 = im[expanded_y1:expanded_y2, expanded_x1:expanded_x2]
                img_path = cv2_save_img('word', im2)
                image_bytes = open(img_path, "rb").read()
                result = ocr.classification(image_bytes, png_fix=True)
                if result in target_char_list:
                    for index, target in enumerate(target_list):
                        if result == target[0] and target[0] is not None:
                            x = x1 + (x2 - x1) / 2
                            y = y1 + (y2 - y1) / 2
                            target_list[index][1] = [x, y]
                            count += 1

            if count != target_char_len:
                logger.info(f'文字识别失败,刷新中......')
                await refresh_button.click()
                await asyncio.sleep(random.uniform(2, 4))
                continue

            for char in target_list:
                center_x = char[1][0]
                center_y = char[1][1]
                # 得到网页上的中心点
                x, y = backend_top_left_x + center_x, backend_top_left_y + center_y
                # 点击图片
                await page.mouse.click(x, y)
                await asyncio.sleep(random.uniform(1, 4))

            # 点击确定
            await button.click()
            await asyncio.sleep(random.uniform(2, 4))

        else:
            shape_type = word.split('请选出图中的')[1]
            if shape_type in supported_types:
                logger.info(f'已找到图形,点击中......')
                if shape_type == "圆环":
                    shape_type = shape_type.replace('圆环', '圆形')
                # 获取点的中心点
                center_x, center_y = get_shape_location_by_type(background_img_path, shape_type)
                if center_x is None and center_y is None:
                    logger.info(f'识别失败,刷新中......')
                    await refresh_button.click()
                    await asyncio.sleep(random.uniform(2, 4))
                    continue
                # 得到网页上的中心点
                x, y = backend_top_left_x + center_x, backend_top_left_y + center_y
                # 点击图片
                await page.mouse.click(x, y)
                await asyncio.sleep(random.uniform(1, 4))
                # 点击确定
                await button.click()
                await asyncio.sleep(random.uniform(2, 4))
                continue
            else:
                logger.info(f'不支持{shape_type},刷新中......')
                # 刷新
                await refresh_button.click()
                await asyncio.sleep(random.uniform(2, 4))
                continue


async def sms_recognition(page, user):
    logger.info("开始短信验证码识别")
    if await page.locator('text="手机短信验证"').count() == 0:
        return

    try:
        from config import sms_func
    except ImportError:
        sms_func = "no"

    sms_func = user_datas[user].get("sms_func", sms_func)

    if sms_func not in supported_sms_func:
        raise Exception(f"sms_func只支持{supported_sms_func}")

    if sms_func == "no":
        raise Exception("需要填写验证码")

    logger.info('点击【获取验证码】中')
    await page.click('button.getMsg-btn')
    await asyncio.sleep(1)
    # 自动识别滑块
    await auto_move_slide(page, retry_times=5, slider_selector='div.bg-blue')
    await auto_shape(page, retry_times=30)

    # 识别是否成功发送验证码
    await page.wait_for_selector('button.getMsg-btn:has-text("重新发送")', timeout=3000)
    logger.info("发送短信验证码成功")

    # 手动输入
    # 用户在60S内，手动在终端输入验证码
    if sms_func == "manual_input":
        from inputimeout import inputimeout, TimeoutOccurred
        try:
            verification_code = inputimeout(prompt="请输入验证码：", timeout=60)
        except TimeoutOccurred:
            return

    # 通过调用web_hook的方式来实现全自动输入验证码
    elif sms_func == "web_hook":
        from utils.tools import send_request
        try:
            from config import sms_webhook
        except ImportError:
            sms_webhook = ""
        sms_webhook = user_datas[user].get("sms_webhook", sms_webhook)

        if sms_webhook is None:
            raise Exception(f"sms_webhook未配置")

        headers = {
            'Content-Type': 'application/json',
        }
        data = {"phone_number": user}
        response = await send_request(url=sms_webhook, method="post", headers=headers, data=data)
        verification_code = response['data']['code']

    await asyncio.sleep(1)
    logger.info('填写验证码中...')
    verification_code_input = page.locator('input.acc-input.msgCode')
    for v in verification_code:
        await verification_code_input.type(v, no_wait_after=True)
        await asyncio.sleep(random.random() / 10)

    logger.info('点击提交中...')
    await page.click('a.btn')

async def get_jd_pt_key(playwright: Playwright, user) -> Union[str, None]:
    """
    获取jd的pt_key
    """

    try:
        from config import headless
    except ImportError:
        headless = False

    args = '--no-sandbox', '--disable-setuid-sandbox'
    browser = await playwright.chromium.launch(headless=headless, args=args)
    context = await browser.new_context()

    try:
        page = await context.new_page()
        await page.goto(jd_login_url)
        await page.get_by_text("账号密码登录").click()

        username_input = page.get_by_placeholder("账号名/邮箱/手机号")
        await username_input.click()
        for u in user:
            await username_input.type(u, no_wait_after=True)
            await asyncio.sleep(random.random() / 10)

        password_input = page.get_by_placeholder("请输入密码")
        await password_input.click()
        password = user_datas[user]["password"]
        for p in password:
            await password_input.type(p, no_wait_after=True)
            await asyncio.sleep(random.random() / 10)

        await page.get_by_role("checkbox").check()
        await page.get_by_text("登 录").click()

        # 自动识别移动滑块验证码
        if auto_move:
            # 关键的sleep
            await asyncio.sleep(1)
            await auto_move_slide(page, retry_times=5)

            # 自动验证形状验证码
            if auto_shape_recognition:
                await asyncio.sleep(1)
                await auto_shape(page, retry_times=30)

            # 进行短信验证识别
            await asyncio.sleep(1)
            await sms_recognition(page, user)

        # 等待验证码通过
        logger.info("等待获取cookie...")
        await page.wait_for_selector('#msShortcutMenu', state='visible', timeout=120000)

        cookies = await context.cookies()
        for cookie in cookies:
            if cookie['name'] == 'pt_key':
                pt_key = cookie["value"]
                return pt_key

        return None

    except Exception as e:
        traceback.print_exc()
        return None

    finally:
        await context.close()
        await browser.close()


async def get_ql_api(ql_data):
    """
    封装了QL的登录
    """
    logger.info("开始获取QL登录态......")

    # 优化client_id和client_secret
    client_id = ql_data.get('client_id')
    client_secret = ql_data.get('client_secret')
    if client_id and client_secret:
        logger.info("使用client_id和client_secret登录......")
        qlapi = QlOpenApi(ql_data["url"])
        response = await qlapi.login(client_id=client_id, client_secret=client_secret)
        if response['code'] == 200:
            logger.info("client_id和client_secret正常可用......")
            return qlapi
        else:
            logger.info("client_id和client_secret异常......")

    qlapi = QlApi(ql_data["url"])

    # 其次用token
    token = ql_data.get('token')
    if token:
        logger.info("已设置TOKEN,开始检测TOKEN状态......")
        qlapi.login_by_token(token)

        # 如果token失效，就用账号密码登录
        response = await qlapi.get_envs()
        if response['code'] == 401:
            logger.info("Token已失效, 正使用账号密码获取QL登录态......")
            response = await qlapi.login_by_username(ql_data.get("username"), ql_data.get("password"))
            if response['code'] != 200:
                logger.error(f"账号密码登录失败. response: {response}")
                raise Exception(f"账号密码登录失败. response: {response}")
        else:
            logger.info("Token正常可用......")
    else:
        # 最后用账号密码
        logger.info("正使用账号密码获取QL登录态......")
        response = await qlapi.login_by_username(ql_data.get("username"), ql_data.get("password"))
        if response['code'] != 200:
            logger.error(f"账号密码登录失败. response: {response}")
            raise Exception(f"账号密码登录失败.response: {response}")
    return qlapi


async def main():
    try:
        qlapi = await get_ql_api(qinglong_data)
        send_api = SendApi("ql")
        # 拿到禁用的用户列表
        response = await qlapi.get_envs()
        if response['code'] == 200:
            logger.info("获取环境变量成功")
        else:
            logger.error(f"获取环境变量失败， response: {response}")
            raise Exception(f"获取环境变量失败， response: {response}")

        user_info = response['data']
        # 获取禁用用户
        forbidden_users = [x for x in user_info if x['name'] == 'JD_COOKIE' and x['status'] == 1]

        if not forbidden_users:
            logger.info("所有COOKIE环境变量正常，无需更新")
            return

        # 获取需要的字段
        filter_users_list = filter_forbidden_users(forbidden_users, ['id', 'value', 'remarks', 'name'])

        # 生成字典
        user_dict = get_forbidden_users_dict(filter_users_list, user_datas)

        # 登录JD获取pt_key
        async with async_playwright() as playwright:
            for user in user_dict:
                logger.info(f"开始更新{user}")
                pt_key = await get_jd_pt_key(playwright, user)
                if pt_key is None:
                    logger.error(f"获取pt_key失败")
                    await send_msg(send_api, send_type=1, msg=f"{user} 更新失败")
                    continue

                req_data = user_dict[user]
                req_data["value"] = f"pt_key={pt_key};pt_pin={user_datas[user]['pt_pin']};"
                logger.info(f"更新内容为{req_data}")
                data = json.dumps(req_data)
                response = await qlapi.set_envs(data=data)
                if response['code'] == 200:
                    logger.info(f"{user}更新成功")
                else:
                    logger.error(f"{user}更新失败, response: {response}")
                    await send_msg(send_api, send_type=1, msg=f"{user} 更新失败")
                    continue

                data = bytes(f"[{req_data['id']}]", 'utf-8')
                response = await qlapi.envs_enable(data=data)
                if response['code'] == 200:
                    logger.info(f"{user}启用成功")
                    await send_msg(send_api, send_type=0, msg=f"{user} 更新成功")
                else:
                    logger.error(f"{user}启用失败, response: {response}")

    except Exception as e:
        traceback.print_exc()


if __name__ == '__main__':
    asyncio.run(main())