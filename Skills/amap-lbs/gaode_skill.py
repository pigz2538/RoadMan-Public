#!/usr/bin/env python3
"""
高德地图 JSAPI Agent Skill
通过 Unix Domain Socket 与 Electron 桌面应用通信，支持导航和 POI 搜索场景。

用法：
    python gaode_skill.py direction 北京站 天安门
    python gaode_skill.py direction 北京站 天安门 driving
    python gaode_skill.py direction 116.397428,39.90923 天安门 walking
    python gaode_skill.py search 北京站周边的川菜
"""

import sys
import json
import socket
import time
import os

SOCKET_PATH = "/tmp/jsapi-electron.sock"
SOCKET_TIMEOUT_SECONDS = 30
VALID_ROUTE_TYPES = {"driving", "walking", "bicycling"}


def send_command(command_payload: dict) -> dict:
    """
    连接到 Electron 应用的 Unix Domain Socket，发送命令并等待结果。

    Args:
        command_payload: 要发送的命令字典，包含 cmd、params、requestId 字段。

    Returns:
        Electron 应用返回的结果字典。

    Raises:
        FileNotFoundError: Socket 文件不存在（应用未启动）。
        TimeoutError: 等待响应超时。
        ConnectionError: 连接或通信失败。
    """
    if not os.path.exists(SOCKET_PATH):
        raise FileNotFoundError(
            f"Socket 文件不存在：{SOCKET_PATH}\n"
            "请先启动高德地图 Electron 应用（cd APP/jsapi-electron-app && npm start）"
        )

    client_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client_socket.settimeout(SOCKET_TIMEOUT_SECONDS)

    try:
        client_socket.connect(SOCKET_PATH)

        message = json.dumps(command_payload, ensure_ascii=False) + "\n"
        client_socket.sendall(message.encode("utf-8"))

        # 读取响应（按换行符分割）
        response_buffer = ""
        while True:
            chunk = client_socket.recv(4096)
            if not chunk:
                break
            response_buffer += chunk.decode("utf-8")
            if "\n" in response_buffer:
                break

        response_line = response_buffer.split("\n")[0].strip()
        if not response_line:
            raise ConnectionError("收到空响应")

        return json.loads(response_line)

    except socket.timeout:
        raise TimeoutError(
            f"等待 Electron 应用响应超时（{SOCKET_TIMEOUT_SECONDS}s），"
            "请确认应用正在运行且地图已加载完成。"
        )
    except json.JSONDecodeError as error:
        raise ConnectionError(f"响应 JSON 解析失败：{error}，原始内容：{response_buffer!r}")
    finally:
        client_socket.close()


def build_request_id() -> str:
    """生成唯一的请求 ID。"""
    return f"skill-{int(time.time() * 1000)}"


def format_direction_result(result: dict) -> str:
    """将导航命令的结果格式化为可读文本。"""
    if not result.get("success"):
        return f"❌ 导航失败：{result.get('error', '未知错误')}"

    origin_lnglat = result.get("originLnglat", [])
    destination_lnglat = result.get("destinationLnglat", [])
    route_type_labels = {"driving": "驾车", "walking": "步行", "bicycling": "骑行"}
    route_type = result.get("routeType", "driving")
    route_label = route_type_labels.get(route_type, route_type)

    lines = [
        f"✅ {route_label}路线规划成功",
        f"",
        f"📍 起点：{result.get('originName', '—')}",
        f"   坐标：{origin_lnglat[0]:.6f}, {origin_lnglat[1]:.6f}" if len(origin_lnglat) == 2 else "   坐标：—",
        f"🏁 终点：{result.get('destinationName', '—')}",
        f"   坐标：{destination_lnglat[0]:.6f}, {destination_lnglat[1]:.6f}" if len(destination_lnglat) == 2 else "   坐标：—",
        f"",
        f"🗺️  路线已在地图上展示。",
    ]
    return "\n".join(lines)


def format_search_result(result: dict) -> str:
    """将搜索命令的结果格式化为可读文本（带序号，方便大模型继续选择）。"""
    if not result.get("success"):
        return f"❌ 搜索失败：{result.get('error', '未知错误')}"

    pois = result.get("pois", [])
    total = result.get("total", 0)
    keywords = result.get("params", {}).get("keywords", "")

    if total == 0:
        return f"🔍 搜索「{keywords}」未找到相关结果。"

    lines = [
        f"🔍 搜索「{keywords}」，共找到 {total} 个结果：",
        f"",
    ]

    for poi in pois:
        index = poi.get("index", "?")
        name = poi.get("name", "—")
        address = poi.get("address") or "暂无地址"
        poi_type = poi.get("type", "").split(";")[0] if poi.get("type") else ""
        location = poi.get("location", "")

        lines.append(f"{index}. {name}")
        lines.append(f"   📌 {address}")
        if poi_type:
            lines.append(f"   🏷️  {poi_type}")
        if location:
            lines.append(f"   🌐 {location}")
        lines.append("")

    lines.append("💡 POI 已在地图上标注，可点击查看详情。")
    return "\n".join(lines)


def run_direction(origin: str, destination: str, route_type: str = "driving") -> None:
    """
    执行导航场景：解析起终点并在地图上展示路线。

    Args:
        origin: 起点，可以是地名或坐标（格式：经度,纬度）。
        destination: 终点，可以是地名或坐标（格式：经度,纬度）。
        route_type: 路线类型，driving / walking / bicycling，默认 driving。
    """
    if route_type not in VALID_ROUTE_TYPES:
        print(f"⚠️  不支持的路线类型「{route_type}」，已自动切换为 driving。")
        route_type = "driving"

    payload = {
        "cmd": "direction",
        "requestId": build_request_id(),
        "params": {
            "origin": origin,
            "destination": destination,
            "type": route_type,
        },
    }

    print(f"🚀 发送导航指令：{origin} → {destination}（{route_type}）")

    result = send_command(payload)
    print(format_direction_result(result))


def run_search(keywords: str) -> None:
    """
    执行搜索场景：在地图上展示 POI 搜索结果。

    Args:
        keywords: 搜索关键词，例如「北京站周边的川菜」。
    """
    payload = {
        "cmd": "search",
        "requestId": build_request_id(),
        "params": {
            "keywords": keywords,
        },
    }

    print(f"🔍 发送搜索指令：{keywords}")

    result = send_command(payload)
    print(format_search_result(result))


def print_usage() -> None:
    """打印使用说明。"""
    usage = """
高德地图 JSAPI Agent Skill

用法：
  python gaode_skill.py direction <起点> <终点> [路线类型]
  python gaode_skill.py search <搜索关键词>

路线类型（可选，默认 driving）：
  driving    驾车
  walking    步行
  bicycling  骑行

示例：
  python gaode_skill.py direction 北京站 天安门
  python gaode_skill.py direction 北京站 天安门 driving
  python gaode_skill.py direction 116.397428,39.90923 天安门 walking
  python gaode_skill.py search 北京站周边的川菜

注意：
  - 起点/终点可以是地名，也可以是「经度,纬度」格式的坐标
  - 使用前请确保 Electron 应用已启动并加载完成
    """.strip()
    print(usage)


def main() -> None:
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        print_usage()
        sys.exit(0)

    command = args[0].lower()

    try:
        if command == "direction":
            if len(args) < 3:
                print("❌ direction 命令需要提供起点和终点。")
                print("   示例：python gaode_skill.py direction 北京站 天安门")
                sys.exit(1)

            origin = args[1]
            destination = args[2]
            route_type = args[3].lower() if len(args) >= 4 else "driving"
            run_direction(origin, destination, route_type)

        elif command == "search":
            if len(args) < 2:
                print("❌ search 命令需要提供搜索关键词。")
                print("   示例：python gaode_skill.py search 北京站周边的川菜")
                sys.exit(1)

            # 支持多个词拼接为完整关键词
            keywords = " ".join(args[1:])
            run_search(keywords)

        else:
            print(f"❌ 未知命令：{command}")
            print("   支持的命令：direction、search")
            print("   使用 -h 查看帮助。")
            sys.exit(1)

    except FileNotFoundError as error:
        print(f"❌ {error}")
        sys.exit(1)
    except TimeoutError as error:
        print(f"⏱️  {error}")
        sys.exit(1)
    except ConnectionError as error:
        print(f"🔌 连接错误：{error}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n已取消。")
        sys.exit(0)


if __name__ == "__main__":
    main()
