#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import yaml
import requests
import re
import sys
from collections import defaultdict
import json
import jsonschema

# ==================== 配置区 ====================
ORIGINAL_YAML = r"D:\Database\Project\Config\clash_self.yaml"        # 原始 Clash 配置文件路径
CUSTOM_INI = r"D:\Database\Project\Config\self_config.ini"            # 自定义规则配置文件路径
OUTPUT_YAML = r"D:\Database\Project\Config\self_conf_new.yaml"      # 输出文件路径（可改为覆盖原文件）
SCHEMA_FILE   = r"D:\Database\Project\Config\meta-json-schema.json"
TIMEOUT = 15                         # 下载规则超时时间
# ================================================

# headers = {
#     #"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
#     "User-Agent": "Clash"
# }
headers = {"User-Agent": "Clash Meta"}

def load_yaml(path):
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def save_yaml(data, path):
    with open(path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False, indent=2, width=9999)

def download_text(url):
    try:
        r = requests.get(url, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"[-] 下载失败 {url}: {e}")
        return ""

def extract_payload(text):
    """支持 clash-classic / ios_rule_script 的 payload 格式"""
    try:
        data = yaml.safe_load(text)
        payload = data.get("payload", [])
        rules = []
        for item in payload:
            if isinstance(item, str):
                rule = item.strip()
                if rule and not rule.startswith('#'):
                    rule = re.sub(r'^\s*-\s*', '', rule)  # 去掉开头的 -
                    rules.append(rule)
        return rules
    except:
        return []

def download_ruleset(source):
    if not source.startswith("http"):
        return []
    text = download_text(source)
    if "payload:" in text.lower():
        return extract_payload(text)
    # 普通纯文本规则
    return [line.strip() for line in text.splitlines()
            if line.strip() and not line.startswith(('#', ';'))]

def parse_custom_ini(path):
    rulesets = defaultdict(list)   # group_name -> [source1, source2, ...]
    proxy_groups = []

    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(('#', ';')):
                continue
            if line.startswith('ruleset='):
                val = line[8:].strip()
                if ',' not in val: continue
                group, src = [x.strip() for x in val.split(',', 1)]
                src = re.sub(r'^(clash|mihomo|clash-classic):', '', src)
                rulesets[group].append(src)
            elif line.startswith('custom_proxy_group='):
                proxy_groups.append(line[19:].strip())

    return rulesets, proxy_groups

def generate_proxy_groups(defs, all_proxy_names):
    groups = []
    for definition in defs:
        parts = [p.strip() for p in definition.split('`')]
        name = parts[0]
        gtype = parts[1].lower()
        g = {"name": name, "type": gtype}

        if gtype == "select":
            proxies = []
            for p in parts[2:]:
                if p.startswith('[]'):
                    target = p[2:]
                    proxies.append("DIRECT" if target == "DIRECT" else target)
                else:
                    proxies.append(p)
            g["proxies"] = proxies

        elif gtype in ["url-test", "fallback", "load-balance"]:
            proxies = []
            url = "http://www.gstatic.com/generate_204"
            interval = 300
            tolerance = 0

            for p in parts[2:]:
                if p.startswith('[]'):
                    target = p[2:]
                    proxies.append("DIRECT" if target == "DIRECT" else target)
                elif p.startswith('(') and p.endswith(')'):
                    pattern = p[1:-1]
                    matched = [n for n in all_proxy_names if re.search(pattern, n, re.I)]
                    proxies.extend(matched)
                elif p.startswith('http'):
                    url = p
                elif ',' in p:
                    i, t = map(str.strip, p.split(',', 1))
                    interval = int(i)
                    if t: tolerance = int(t)
                else:
                    try: interval = int(p)
                    except: pass

            g["proxies"] = proxies or ["DIRECT"]
            g["url"] = url
            g["interval"] = interval
            if tolerance: g["tolerance"] = tolerance

        groups.append(g)
    return groups

def validate_config(config_dict):
    """验证配置是否符合 meta-json-schema.json"""
    with open(SCHEMA_FILE, 'r', encoding='utf-8') as f:
        schema = json.load(f)
    jsonschema.validate(instance=config_dict, schema=schema)
    print("[+] 配置验证通过 meta-json-schema.json")

def main():
    print("[+] 正在加载原始配置文件...")
    config = load_yaml(ORIGINAL_YAML)
    proxies = config.get("proxies", [])
    proxy_names = [p.get("name") for p in proxies if p.get("name")]

    print(f"[+] 发现 {len(proxy_names)} 个节点")

    rulesets, pg_defs = parse_custom_ini(CUSTOM_INI)

    new_rules = []

    print("[+] 处理规则集...")
    for group_name, sources in rulesets.items():
        print(f"  → {group_name} ← {len(sources)} 个来源")
        for src in sources:
            if src.upper().startswith("[]GEOIP,"):
                country = src.split(",", 1)[1].strip().upper()
                new_rules.append(f"GEOIP,{country},{group_name}")
                continue

            lines = download_ruleset(src)
            for line in lines:
                line = line.strip()
                if not line: continue

                if re.match(r'^(USER-AGENT)', line, re.I):
                    continue

                if re.match(r'^(MATCH|FINAL|GEOIP|AND|OR|NOT)', line, re.I):
                    new_rules.append(line)
                    continue

                if ',' in line:
                    parts = line.split(",", 2)
                    rule_type = parts[0].strip()
                    payload = parts[1].strip()
                    extra = "," + parts[2] if len(parts) > 2 else ""
                    new_rules.append(f"{rule_type},{payload},{group_name}{extra}")
                else:
                    new_rules.append(f"{line},{group_name}")

    new_rules.extend([
        "GEOIP,CN,🎯 直连",
        "MATCH,🐟 漏网之鱼"
    ])

    print("[+] 生成 proxy-groups...")
    new_groups = generate_proxy_groups(pg_defs, proxy_names)

    result = config.copy()
    result["rules"] = new_rules
    result["proxy-groups"] = new_groups

    print("[+] 验证生成的配置...")

    save_yaml(result, OUTPUT_YAML)

    validate_config(result)

    
    print(f"[+] 完成！新配置文件已保存：{OUTPUT_YAML}")

if __name__ == "__main__":
    main()