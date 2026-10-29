"""Auditable AISM structure used as context, never as a second semantic extractor.

UP peels result/sink layers using R=R∩Q; DOWN peels cause/source layers using
Q=R∩Q. Layers are extraction order, not numerical preference weights. Cycles
remain strongly connected components. See docs/aism-semantic-design-20261028.md.
"""
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path

from schemas import EXTRACTION_CRITERIA

ALGORITHM_VERSION = 'aism-closure-up-down-v1'
MODEL_PATH = Path(__file__).resolve().parent/'data/aism-semantic-structure.json'


def analyse_structure(nodes, edges):
    """Analyse at most 32 named nodes with binary directed relationships."""
    if (not isinstance(nodes, (list, tuple)) or not 1 <= len(nodes) <= 32
            or any(not isinstance(n, str) or not n or len(n) > 128 for n in nodes)
            or len(set(nodes)) != len(nodes)):
        raise ValueError('AISM nodes must be 1–32 distinct nonempty string identifiers')
    if not isinstance(edges, (list, tuple)) or len(edges) > len(nodes)**2:
        raise ValueError('AISM edges must be a bounded list of node pairs')
    index = {node: i for i, node in enumerate(nodes)}
    n = len(nodes)
    adjacency = [[0]*n for _ in nodes]
    for edge in edges:
        if (not isinstance(edge, (list, tuple)) or len(edge) != 2
                or not all(isinstance(v, str) and v in index for v in edge)):
            raise ValueError('AISM edge endpoints must reference known nodes')
        adjacency[index[edge[0]]][index[edge[1]]] = 1
    reach = [[int(i == j or adjacency[i][j]) for j in range(n)] for i in range(n)]
    for k in range(n):
        for i in range(n):
            if reach[i][k]:
                for j in range(n):
                    reach[i][j] = int(reach[i][j] or reach[k][j])

    def layers(direction):
        remaining, output = set(range(n)), []
        while remaining:
            chosen = []
            for i in sorted(remaining):
                reachable = {j for j in remaining if reach[i][j]}
                antecedent = {j for j in remaining if reach[j][i]}
                common = reachable & antecedent
                if (reachable if direction == 'up' else antecedent) == common:
                    chosen.append(i)
            if not chosen:  # A reflexive transitive relation always has extremal SCCs.
                raise ValueError('AISM layer extraction could not progress')
            output.append([nodes[i] for i in chosen])
            remaining.difference_update(chosen)
        return output

    components, assigned = [], set()
    for i in range(n):
        if i not in assigned:
            component = [j for j in range(n) if reach[i][j] and reach[j][i]]
            components.append(component); assigned.update(component)
    representatives = [group[0] for group in components]
    skeleton = []
    for a, group_a in enumerate(components):
        for b, group_b in enumerate(components):
            if a == b or not any(adjacency[i][j] for i in group_a for j in group_b):
                continue
            u, v = representatives[a], representatives[b]
            if not any(c not in (a, b) and reach[u][w] and reach[w][v]
                       for c, w in enumerate(representatives)):
                skeleton.append({'source': [nodes[i] for i in group_a],
                                 'target': [nodes[i] for i in group_b]})
    return {'adjacency_matrix': adjacency, 'reachability_matrix': reach,
            'up_layers': layers('up'), 'down_layers': layers('down'),
            'strong_components': [[nodes[i] for i in group] for group in components],
            'skeleton_edges': skeleton}


def build_semantic_structure(config):
    """Validate/version a project model; graph relations are distinct from preferences."""
    if not isinstance(config, dict):
        raise ValueError('AISM model must be an object')
    nodes, relations = config.get('nodes'), config.get('relations')
    if (not isinstance(nodes, list) or len(nodes) != len(EXTRACTION_CRITERIA)
            or any(not isinstance(n, dict) for n in nodes)):
        raise ValueError('AISM project model must define all seven dimensions')
    for node in nodes:
        if any(not isinstance(node.get(key), str) or not node[key] or len(node[key]) > 1024
               for key in ('id', 'label', 'definition')):
            raise ValueError('AISM dimensions need bounded identifiers, labels and definitions')
    if [n['id'] for n in nodes] != list(EXTRACTION_CRITERIA):
        raise ValueError('AISM project model must preserve the canonical seven-dimension schema')
    if not isinstance(relations, list) or any(not isinstance(r, dict) for r in relations):
        raise ValueError('AISM relations must be objects')
    for relation in relations:
        if not isinstance(relation.get('basis'), str) or not relation['basis']:
            raise ValueError('Each AISM relationship requires an explicit basis')
    if (not isinstance(config.get('model_version'), str) or not config['model_version']
            or not isinstance(config.get('basis_type'), str) or not config['basis_type']
            or type(config.get('empirically_validated')) is not bool):
        raise ValueError('AISM model needs version, basis and explicit validation status')
    analysis = analyse_structure([n['id'] for n in nodes],
                                 [(r.get('source'), r.get('target')) for r in relations])
    canonical = json.dumps({'algorithm_version': ALGORITHM_VERSION, 'model': config},
                           ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return {**deepcopy(config), **analysis, 'algorithm_version': ALGORITHM_VERSION,
            'model_sha256': hashlib.sha256(canonical.encode('utf-8')).hexdigest()}


@lru_cache(maxsize=1)
def _snapshot():
    # Immutable process snapshot: prompts and returned metadata cannot drift apart.
    return build_semantic_structure(json.loads(MODEL_PATH.read_text(encoding='utf-8')))


def semantic_structure():
    return deepcopy(_snapshot())


def aism_prompt_context():
    model = _snapshot()
    labels = {node['id']: node['label'] for node in model['nodes']}
    lines = ['## AISM维度结构（抽取上下文，不是第二个语义抽取器）',
             f"结构版本：{model['model_version']}；算法：{model['algorithm_version']}",
             f"结构摘要：{model['model_sha256']}",
             '初始结构来自本项目现有定义和规格评分依赖，未经语义效果实验证明；不作为原话真实性判据。',
             '按下列维度定义逐项抽取，仍需输出七项判断，不得漏掉独立节点：']
    lines.extend(f"- {node['id']}（{node['label']}）：{node['definition']}" for node in model['nodes'])
    lines.append('有向关系只表示规格参考分的计算依赖，不代表用户偏好推断：')
    lines.extend(f"- {labels[r['source']]} → {labels[r['target']]}：{r['basis']}" for r in model['relations'])
    for key, title in (('up_layers', 'UP结果优先，按结果端逐层剥离'),
                       ('down_layers', 'DOWN原因优先，按输入端逐层剥离')):
        description = '；'.join(f"第{i+1}层：" + '、'.join(labels[node] for node in layer)
                                for i, layer in enumerate(model[key]))
        lines.append(f'{title}：{description}。')
    lines.extend(['层级不是重要程度；用户重视game不自动增加chipset或battery偏好。',
                  '用户未明确表达的相关维度仍填未提及，系统初始权重由程序处理；不生成数值权重。',
                  '预算、品牌以及存储门槛/尺寸参数继续按原有硬条件字段抽取。',
                  'AISM不能单独判断某句话是否真的支持抽取结果，仍保留原话证据与用户整体确认。'])
    return '\n'.join(lines)
