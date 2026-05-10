#!/usr/bin/env python3
"""Ежедневный отчёт по ставкам и лотереям"""
import csv, os
from datetime import datetime, date

BETTING = os.path.expanduser('~/.openclaw/workspace/betting/bets.csv')
LOTTERY = os.path.expanduser('~/.openclaw/workspace/lottery/tickets.csv')

def load_csv(path):
    with open(path) as f:
        return list(csv.DictReader(f))

def money(v):
    v = int(float(v or 0))
    return f'{v:,} ₽'.replace(',', ' ')

def report():
    bets = load_csv(BETTING)
    tickets = load_csv(LOTTERY)

    lines = ['📊 **Ежедневный отчёт: ставки и лотереи**', '']

    # Ставки
    total_stake = sum(int(r['stake'] or 0) for r in bets)
    won_bets = [r for r in bets if r['status'] == 'won']
    lost_bets = [r for r in bets if r['status'] == 'lost']
    pending_bets = [r for r in bets if r['status'] == 'pending']
    total_return = sum(int(r['return'] or 0) for r in won_bets)
    net = total_return - total_stake

    lines.append(f'**🎲 Ставки (bets.csv — {len(bets)} всего)**')
    lines.append(f'— Поставлено: {money(total_stake)}')
    lines.append(f'— Возврат: {money(total_return)}')
    lines.append(f'— Чистый: {money(abs(net))} {"📈" if net >= 0 else "📉"}')
    lines.append(f'— Выиграно: {len(won_bets)} | Проиграно: {len(lost_bets)} | Ожидают: {len(pending_bets)}')
    lines.append('')

    # Последние события
    recent = [r for r in bets if r['date'] == date.today().strftime('%Y-%m-%d')]
    if recent:
        lines.append('**Сегодняшние события:**')
        for r in recent:
            emoji = {'won':'✅','lost':'❌','pending':'⏳'}.get(r['status'],'❓')
            lines.append(f'{emoji} {r["match"]} — {r["selection"]} — {money(r["stake"])} x {r["odds"]} → {money(r["return"])}')
        lines.append('')

    # Лотереи
    total_cost = sum(int(t['cost'] or 0) for t in tickets)
    won_t = [t for t in tickets if t['status'] == 'won']
    pending_t = [t for t in tickets if t['status'] == 'pending']
    total_prize = sum(int(t['prize'] or 0) for t in won_t)
    lot_net = total_prize - total_cost

    lines.append(f'**🎰 Лотереи (lottery/tickets.csv — {len(tickets)} билетов)**')
    lines.append(f'— Потрачено: {money(total_cost)}')
    lines.append(f'— Выиграно: {money(total_prize)}')
    lines.append(f'— Чистый: {money(abs(lot_net))} {"📈" if lot_net >= 0 else "📉"}')
    lines.append(f'— Выигрышей: {len(won_t)} | Ожидают: {len(pending_t)}')

    if pending_t:
        lines.append('')
        lines.append('**Ожидают розыгрыша:**')
        for t in pending_t:
            lines.append(f'⏳ {t["game"]}, тираж {t["draw"]}, билет {t["ticket_number"]}, {money(t["cost"])}')

    return '\n'.join(lines)

if __name__ == '__main__':
    print(report())
