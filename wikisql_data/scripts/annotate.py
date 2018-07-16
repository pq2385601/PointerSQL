#!/usr/bin/env python
from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser
import os
import records
import ujson as json
from stanza.nlp.corenlp import CoreNLPClient
from tqdm import tqdm
import copy
from lib.common import count_lines, detokenize
from lib.query import Query

from pprint import pprint

client = None


def annotate(sentence, lower=True):
    global client
    if client is None:
        client = CoreNLPClient(default_annotators='ssplit,tokenize'.split(','))
    words, gloss, after = [], [], []
    for s in client.annotate(sentence):
        for t in s:
            words.append(t.word)
            gloss.append(t.originalText)
            after.append(t.after)
    if lower:
        words = [w.lower() for w in words]
    return {
        'gloss': gloss,
        'words': words,
        'after': after,
        }


def annotate_example(example, table):
    ann = {'table_id': example['table_id']}
    ann['question'] = annotate(example['question'])
    ann['table'] = {
        'header': [annotate(h) for h in table['header']],
    }
    ann['query'] = sql = copy.deepcopy(example['sql'])
    for c in ann['query']['conds']:
        c[-1] = annotate(str(c[-1]))

    q1 = 'SYMSELECT SYMAGG {} SYMCOL {}'.format(Query.agg_ops[sql['agg']], table['header'][sql['sel']])
    q2 = ['SYMCOL {} SYMOP {} SYMCOND {}'.format(table['header'][col], Query.cond_ops[op], detokenize(cond)) for col, op, cond in sql['conds']]
    if q2:
        q2 = 'SYMWHERE ' + ' SYMAND '.join(q2) + ' SYMEND'
    else:
        q2 = 'SYMEND'
    inp = 'SYMSYMS {syms} SYMAGGOPS {aggops} SYMCONDOPS {condops} SYMTABLE {table} SYMQUESTION {question} SYMEND'.format(
        syms=' '.join(['SYM' + s for s in Query.syms]),
        table=' '.join(['SYMCOL ' + s for s in table['header']]),
        question=example['question'],
        aggops=' '.join([s for s in Query.agg_ops]),
        condops=' '.join([s for s in Query.cond_ops]),
    )
    ann['seq_input'] = annotate(inp)
    out = '{q1} {q2}'.format(q1=q1, q2=q2) if q2 else q1
    ann['seq_output'] = annotate(out)
    ann['where_output'] = annotate(q2)
    assert 'symend' in ann['seq_output']['words']
    assert 'symend' in ann['where_output']['words']
    return ann


def is_valid_example(e):
    if not all([h['words'] for h in e['table']['header']]):
        return False
    headers = [detokenize(h).lower() for h in e['table']['header']]
    if len(headers) != len(set(headers)):
        return False
    input_vocab = set(e['seq_input']['words'])
    for w in e['seq_output']['words']:
        if w not in input_vocab:
            print('query word "{}" is not in input vocabulary.\n{}'.format(w, e['seq_input']['words']))
            return False
    input_vocab = set(e['question']['words'])
    for col, op, cond in e['query']['conds']:
        for w in cond['words']:
            if w not in input_vocab:
                print('cond word "{}" is not in input vocabulary.\n{}'.format(w, e['question']['words']))
                return False
    return True


def process_tables(ftable, fout=None):

    # join words together
    def _join_words(entry):
        result = ""
        for i in range(len(entry["words"])):
            result += entry["words"][i] + (entry["after"][i] if entry["after"][i] is not " " else "^")
        return result

    tables = []
    with open(ftable) as ft:
        for line in tqdm(ft, total=count_lines(ftable)):
            raw_table = json.loads(line)
            try:
                table = {
                    "id": raw_table["id"],
                    "header": [ _join_words(annotate(h)) for h in raw_table["header"]],
                    "content": [[ _join_words(annotate(str(tok))) for tok in l] for l in raw_table["rows"]]
                }
                tables.append(table)
            except:
                print(line)
                break

    if fout is not None:
        with open(fout, "w+") as fo:
            for table in tables:
                fo.write(json.dumps(table) + "\n")


def process_examples(fexample, ftable, fout):
    print('annotating {}'.format(fexample))
    with open(fexample) as fs, open(ftable) as ft, open(fout, 'wt') as fo:
        print('loading tables')
        tables = {}
        for line in tqdm(ft, total=count_lines(ftable)):
            d = json.loads(line)
            tables[d['id']] = d
        print('loading examples')
        n_written = 0
        for line in tqdm(fs, total=count_lines(fexample)):
            d = json.loads(line)
            a = annotate_example(d, tables[d['table_id']])
            if not is_valid_example(a):
                print(str(a))
                continue
                raise Exception(str(a))

            gold = Query.from_tokenized_dict(a['query'])
            reconstruct = Query.from_sequence(a['seq_output'], a['table'], lowercase=True)
            if gold.lower() != reconstruct.lower():
                raise Exception ('Expected:\n{}\nGot:\n{}'.format(gold, reconstruct))
            fo.write(json.dumps(a) + '\n')
            #print(a)
            #break
            n_written += 1
        print('wrote {} examples'.format(n_written))


if __name__ == '__main__':
    parser = ArgumentParser(formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument('--din', default=os.path.join('..', 'data'), help='data directory')
    parser.add_argument('--dout', default=os.path.join('..', 'annotated'), help='output directory')
    args = parser.parse_args()

    if not os.path.isdir(args.dout):
        os.makedirs(args.dout)
    
#    for split in ["dev","test", "train"]:
    for split in ["dev","test", "train"]:
        process_tables(ftable=os.path.join(args.din, split) + '.tables.jsonl',
                       fout=os.path.join(args.dout, split) + '.tables.jsonl')
        process_examples(fexample=os.path.join(args.din, split) + '.jsonl', 
                         ftable=os.path.join(args.din, split) + '.tables.jsonl', 
                         fout=os.path.join(args.dout, split) + '.jsonl')
    
    
