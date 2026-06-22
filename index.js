const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

// 1. АВТОУСТАНОВКА БИБЛИОТЕК
function autoInstall() {
    const packages = ['vk-io', '@vk-io/hear', 'better-sqlite3', 'axios'];
    let missing = [];
    for (let pkg of packages) {
        try { require.resolve(pkg); } catch (e) { missing.push(pkg); }
    }
    if (missing.length > 0) {
        console.log(`📦 Установка библиотек: ${missing.join(', ')}...`);
        execSync(`npm install ${missing.join(' ')}`, { stdio: 'inherit' });
        console.log('✅ Библиотеки установлены! Перезапускаю...');
        execSync(`node "${__filename}"`, { stdio: 'inherit' });
        process.exit(0);
    }
}
autoInstall();

const { VK, Keyboard } = require('vk-io');
const { HearManager } = require('@vk-io/hear');
const Database = require('better-sqlite3');
const axios = require('axios');

// 2. НАСТРОЙКА И КОНФИГ
const CONFIG_PATH = path.join(__dirname, 'config.json');
const DB_PATH = path.join(__dirname, 'vk_manager.db');

function loadConfig() {
    if (fs.existsSync(CONFIG_PATH)) return JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
    return null;
}

let config = loadConfig();
if (!config || !config.VK_TOKEN) {
    config = {
        VK_TOKEN: 'vk1.a.jmhGtKNRy-okO7WM6HyGJofKiJMaUnBDyB3kEqxdKypWpcnJaEB7KBJixSmIMLc7YLBJHu6wKY2sElm6VlK59GWdnir2DJQl5D9ohPLQ_8USyg-_gpviWLw31YaUIcx51Y84dSXBPjUpwIULup3JGkiHECtNOGSqlxX4q3IvWgeGEwzaXefqwmTa9aFx2-g9b5dmx07Wx-HH3-Tu_2HDag',
        OWNER_ID: 848213593,
        CEREBRAS_API_KEY: 'csk-ph2w5j3tthvhrfhd4n6vw4eypkecj58hppf2eef6y5cte3vy',
        CEREBRAS_MODEL: 'llama3.1-8b'
    };
    fs.writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 4));
    console.log('✅ Конфиг с токеном сгенерирован!');
}

const OWNER_ID = config.OWNER_ID || 750694024;
const vk = new VK({ token: config.VK_TOKEN });
const hearManager = new HearManager();
vk.updates.on('message_new', hearManager.middleware);

// 3. БАЗА ДАННЫХ
const db = new Database(DB_PATH);
db.pragma('journal_mode = WAL');

db.exec(`
    CREATE TABLE IF NOT EXISTS users (user_id INTEGER, chat_id INTEGER, role TEXT DEFAULT 'user', balance INTEGER DEFAULT 0, warns INTEGER DEFAULT 0, nickname TEXT, messages INTEGER DEFAULT 0, last_seen INTEGER, PRIMARY KEY (user_id, chat_id));
    CREATE TABLE IF NOT EXISTS punishments (user_id INTEGER, chat_id INTEGER, p_type TEXT, reason TEXT, until INTEGER, issued_by INTEGER, PRIMARY KEY (user_id, chat_id, p_type));
    CREATE TABLE IF NOT EXISTS settings (chat_id INTEGER PRIMARY KEY, welcome TEXT, rules TEXT, antispam INTEGER DEFAULT 1, antimat INTEGER DEFAULT 1, antilink INTEGER DEFAULT 0, anticaps INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, chat_id INTEGER, text TEXT, trigger_at INTEGER);
    CREATE TABLE IF NOT EXISTS notes (chat_id INTEGER, name TEXT, content TEXT, created_by INTEGER, PRIMARY KEY (chat_id, name));
    CREATE TABLE IF NOT EXISTS custom_roles (chat_id INTEGER, name TEXT, level INTEGER, PRIMARY KEY (chat_id, name));
    CREATE TABLE IF NOT EXISTS forbidden_words (chat_id INTEGER, word TEXT, PRIMARY KEY (chat_id, word));
    CREATE TABLE IF NOT EXISTS linked_chats (chat_id INTEGER PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS premium (user_id INTEGER PRIMARY KEY, until INTEGER);
`);

// 4. КОНСТАНТЫ И УТИЛИТЫ
const ROLES = { owner: 100, admin: 80, moderator: 50, helper: 20, user: 0 };
const ANTIMAT_WORDS = ["бля", "сук", "хуй", "пизд", "еба", "нахуй", "чмо"];
const GAME_EMOJIS = ["🍒", "🍋", "💎", "7️⃣", "⭐"];
const MAX_WARNS = 3;

let recentMessages = {}; // Для /clean
let ownerSelectedChat = null; // Для отправки сообщений владельцем

function ensureUser(userId, chatId) {
    db.prepare('INSERT OR IGNORE INTO users (user_id, chat_id) VALUES (?, ?)').run(userId, chatId);
    db.prepare('UPDATE users SET messages = messages + 1, last_seen = ? WHERE user_id = ? AND chat_id = ?').run(Math.floor(Date.now()/1000), userId, chatId);
}

function getRoleLevel(userId, chatId) {
    if (userId === OWNER_ID) return 100;
    const row = db.prepare('SELECT role FROM users WHERE user_id = ? AND chat_id = ?').get(userId, chatId);
    if (!row) return 0;
    if (ROLES[row.role] !== undefined) return ROLES[row.role];
    const custom = db.prepare('SELECT level FROM custom_roles WHERE chat_id = ? AND name = ?').get(chatId, row.role);
    return custom ? custom.level : 0;
}

function getRoleName(userId, chatId) {
    if (userId === OWNER_ID) return "Владелец";
    const row = db.prepare('SELECT role FROM users WHERE user_id = ? AND chat_id = ?').get(userId, chatId);
    return row && row.role ? row.role : "Пользователь";
}

function parseTime(str) {
    const match = (str||"").toLowerCase().match(/^(\d+)([smhdсмчд])?$/);
    if (!match) return 0;
    const val = parseInt(match[1]);
    const mult = { s: 1, с: 1, m: 60, м: 60, h: 3600, ч: 3600, d: 86400, д: 86400 }[match[2] || 'm'];
    return val * mult;
}

async function resolveTarget(context, args) {
    if (context.hasReplyMessage) return context.replyMessage.senderId;
    const match = (args||"").match(/\[id(\d+)\|/i) || (args||"").match(/id(\d+)/i) || (args||"").match(/@(\w+)/i);
    if (!match) return null;
    if (!isNaN(match[1])) return parseInt(match[1]);
    try {
        const res = await vk.api.utils.resolveScreenName({ screen_name: match[1] });
        return res.object_id;
    } catch(e) { return null; }
}

async function kickUser(chatId, userId) {
    try {
        await vk.api.messages.removeChatUser({ chat_id: chatId - 2000000000, member_id: userId });
        db.prepare(`UPDATE users SET role='user' WHERE user_id=? AND chat_id=?`).run(userId, chatId);
        return true;
    } catch(e) { return false; }
}

function formatCoins(amount) { return `🪙 ${parseInt(amount).toLocaleString('ru-RU')}`; }

// 5. MIDDLEWARE (Защита, Трекинг, Антиспам)
vk.updates.on('message_new', async (context, next) => {
    if (context.isOutbox || context.senderId < 0) return next();
    const chatId = context.peerId;
    const userId = context.senderId;

    if (chatId >= 2000000000) {
        ensureUser(userId, chatId);
        if (!recentMessages[chatId]) recentMessages[chatId] = [];
        recentMessages[chatId].push(context.conversationMessageId);
        if (recentMessages[chatId].length > 100) recentMessages[chatId].shift();

        let settings = db.prepare('SELECT * FROM settings WHERE chat_id = ?').get(chatId);
        if (!settings) {
            db.prepare('INSERT INTO settings (chat_id) VALUES (?)').run(chatId);
            settings = { antimat: 1, antilink: 0, anticaps: 0, antispam: 1 };
        }

        const roleLvl = getRoleLevel(userId, chatId);
        const text = (context.text || '').toLowerCase();

        // Проверка бана
        const ban = db.prepare(`SELECT until, reason FROM punishments WHERE user_id=? AND chat_id=? AND p_type='ban'`).get(userId, chatId);
        if (ban && (ban.until === 0 || ban.until > Math.floor(Date.now() / 1000)) && roleLvl < 100) {
            await kickUser(chatId, userId);
            return context.send(`🚫 @id${userId} находится в бане! Авто-кик.`);
        }

        // Проверка мута
        const mute = db.prepare(`SELECT until, reason FROM punishments WHERE user_id=? AND chat_id=? AND p_type='mute'`).get(userId, chatId);
        if (mute && mute.until > Math.floor(Date.now() / 1000) && roleLvl < 100) {
            try { await vk.api.messages.delete({ peer_id: chatId, conversation_message_ids: context.conversationMessageId, delete_for_all: 1 }); } catch (e) {}
            return;
        }

        if (roleLvl < 20) {
            // Антимат
            if (settings.antimat && ANTIMAT_WORDS.some(w => text.includes(w))) {
                await context.send("🚫 Мат запрещен!");
                try { await vk.api.messages.delete({ peer_id: chatId, conversation_message_ids: context.conversationMessageId, delete_for_all: 1 }); } catch (e) {}
                return;
            }
            // Антиссылки
            if (settings.antilink && /(https?:\/\/|vk\.com\/)/i.test(text)) {
                await context.send("🚫 Ссылки запрещены!");
                try { await vk.api.messages.delete({ peer_id: chatId, conversation_message_ids: context.conversationMessageId, delete_for_all: 1 }); } catch (e) {}
                return;
            }
            // Антикапс
            if (settings.anticaps) {
                const letters = context.text.replace(/[^a-zA-Zа-яА-Я]/g, '');
                if (letters.length > 10 && (letters.split('').filter(c => c === c.toUpperCase()).length / letters.length > 0.7)) {
                    await context.send("🚫 Выключи CAPS LOCK!");
                    try { await vk.api.messages.delete({ peer_id: chatId, conversation_message_ids: context.conversationMessageId, delete_for_all: 1 }); } catch (e) {}
                    return;
                }
            }
            // Кастомный фильтр слов
            const fWords = db.prepare(`SELECT word FROM forbidden_words WHERE chat_id=?`).all(chatId);
            if (fWords.some(fw => text.includes(fw.word))) {
                await context.send(`🚫 Слово запрещено фильтром чата.`);
                try { await vk.api.messages.delete({ peer_id: chatId, conversation_message_ids: context.conversationMessageId, delete_for_all: 1 }); } catch (e) {}
                return;
            }
        }
    }

    // ЛС Владельца -> Отправка в выбранный чат
    if (chatId < 2000000000 && userId === OWNER_ID && !context.text.startsWith('/') && ownerSelectedChat) {
        try {
            await vk.api.messages.send({ peer_id: ownerSelectedChat, message: context.text + "\n\nP.S Данил Михайлов", random_id: 0 });
            return context.send("✅ Отправлено в выбранную группу.");
        } catch(e) { return context.send(`❌ Ошибка: ${e.message}`); }
    }

    return next();
});

// 6. ВСЕ КОМАНДЫ (HearManager)

hearManager.hear(/^\/(start|help|помощь)/i, async (context) => {
    const role = getRoleLevel(context.senderId, context.peerId);
    let msg = "🤖 **МЕГА-МЕНЕДЖЕР БОТ**\n\n👤 Пользователь:\n/profile, /stat, /chatinfo, /topchat, /rules, /staff, /report\n🎮 Фан и Экономика:\n/coins, /daily, /shop, /coinflip, /slots, /dice, /calc, /ai, /quote, /joke, /remind\n";
    if (role >= 20) msg += "\n🟡 Модератор:\n/mute, /unmute, /warn, /unwarn, /clearwarns, /kick, /clean, /pin, /unpin, /invite, /save, /note\n/antispam, /antimat, /antilink, /anticaps [on/off]\n/filter [add/del/list]";
    if (role >= 80) msg += "\n\n🔴 Админ:\n/ban, /unban, /masskick, /nick, /removenick, /zov, /gkick, /gmute, /gban";
    if (role >= 100) msg += "\n\n👑 Владелец:\n/setrole, /newrole, /delrole, /linkchat, /groups, /givecoins, /setvip";
    await context.send(msg);
});

// === ПРОФИЛЬ И СТАТИСТИКА ===
hearManager.hear(/^\/(profile|профиль)/i, async (context) => {
    const target = await resolveTarget(context, context.text) || context.senderId;
    const row = db.prepare('SELECT * FROM users WHERE user_id=? AND chat_id=?').get(target, context.peerId) || {};
    const vip = db.prepare('SELECT until FROM premium WHERE user_id=?').get(target);
    const roleName = getRoleName(target, context.peerId);
    await context.send(`🔍 Профиль @id${target}\n🗣 Роль: ${roleName}\n⚠ Варнов: ${row.warns||0}/${MAX_WARNS}\n📄 Ник: ${row.nickname||'нет'}\n💬 Сообщений: ${row.messages||0}\n💎 VIP: ${vip && vip.until > Date.now()/1000 ? 'Да' : 'Нет'}\n🪙 Монет: ${formatCoins(row.balance||0)}`);
});

hearManager.hear(/^\/(chatinfo|стата|topchat)/i, async (context) => {
    const [, cmd] = context.match;
    if (cmd.toLowerCase() === 'topchat') {
        const top = db.prepare('SELECT user_id, messages FROM users WHERE chat_id=? ORDER BY messages DESC LIMIT 10').all(context.peerId);
        return context.send("🏆 ТОП ЧАТА:\n" + top.map((t,i) => `${i+1}. @id${t.user_id} — ${t.messages} смс`).join('\n'));
    }
    const s = db.prepare('SELECT * FROM settings WHERE chat_id=?').get(context.peerId) || {};
    await context.send(`ℹ️ ИНФО О ЧАТЕ\n🆔 ID: ${context.peerId}\n🛡 Антимат: ${s.antimat?'вкл':'выкл'}\n🔗 Антиссылка: ${s.antilink?'вкл':'выкл'}\n🔠 Антикапс: ${s.anticaps?'вкл':'выкл'}`);
});

// === МОДЕРАЦИЯ (Mute, Kick, Ban, Warn) ===
hearManager.hear(/^\/(mute|мут)\s+(.*)/i, async (context) => {
    if (getRoleLevel(context.senderId, context.peerId) < 20) return;
    const target = await resolveTarget(context, context.match[2]);
    if (!target || target === context.senderId || getRoleLevel(target, context.peerId) >= getRoleLevel(context.senderId, context.peerId)) return context.send("❌ Недопустимая цель.");
    const timeStr = context.match[2].split(' ').find(x => parseTime(x) > 0) || "10m";
    const until = Math.floor(Date.now() / 1000) + parseTime(timeStr);
    db.prepare(`INSERT OR REPLACE INTO punishments (user_id, chat_id, p_type, reason, until, issued_by) VALUES (?, ?, 'mute', 'Нарушение', ?, ?)`).run(target, context.peerId, until, context.senderId);
    await context.send(`🔇 @id${target} замучен на ${timeStr}.`);
});

hearManager.hear(/^\/(unmute|размут)\s*(.*)/i, async (context) => {
    if (getRoleLevel(context.senderId, context.peerId) < 20) return;
    const target = await resolveTarget(context, context.match[2]);
    if (target) { db.prepare(`DELETE FROM punishments WHERE user_id=? AND chat_id=? AND p_type='mute'`).run(target, context.peerId); await context.send(`🔊 @id${target} размучен.`); }
});

hearManager.hear(/^\/(kick|кик)\s*(.*)/i, async (context) => {
    if (getRoleLevel(context.senderId, context.peerId) < 50) return;
    const target = await resolveTarget(context, context.match[2]);
    if (!target || getRoleLevel(target, context.peerId) >= getRoleLevel(context.senderId, context.peerId)) return context.send("❌ Ошибка прав/цели.");
    if (await kickUser(context.peerId, target)) await context.send(`👢 @id${target} кикнут.`);
});

hearManager.hear(/^\/(ban|бан)\s*(.*)/i, async (context) => {
    if (getRoleLevel(context.senderId, context.peerId) < 80) return;
    const target = await resolveTarget(context, context.match[2]);
    if (!target || getRoleLevel(target, context.peerId) >= getRoleLevel(context.senderId, context.peerId)) return context.send("❌ Ошибка.");
    const timeStr = context.match[2].split(' ').find(x => parseTime(x) > 0) || "0";
    const until = timeStr === "0" ? 0 : Math.floor(Date.now() / 1000) + parseTime(timeStr);
    db.prepare(`INSERT OR REPLACE INTO punishments (user_id, chat_id, p_type, until, issued_by) VALUES (?, ?, 'ban', ?, ?)`).run(target, context.peerId, until, context.senderId);
    await kickUser(context.peerId, target);
    await context.send(`🚫 @id${target} забанен ${until ? 'на '+timeStr : 'навсегда'}.`);
});

hearManager.hear(/^\/(unban|разбан)\s*(.*)/i, async (context) => {
    if (getRoleLevel(context.senderId, context.peerId) < 80) return;
    const target = await resolveTarget(context, context.match[2]);
    if (target) { db.prepare(`DELETE FROM punishments WHERE user_id=? AND chat_id=? AND p_type='ban'`).run(target, context.peerId); await context.send(`✅ @id${target} разбанен.`); }
});

hearManager.hear(/^\/(warn|пред)\s*(.*)/i, async (context) => {
    if (getRoleLevel(context.senderId, context.peerId) < 20) return;
    const target = await resolveTarget(context, context.match[2]);
    if (!target || getRoleLevel(target, context.peerId) >= getRoleLevel(context.senderId, context.peerId)) return context.send("❌ Ошибка.");
    db.prepare('UPDATE users SET warns = warns + 1 WHERE user_id=? AND chat_id=?').run(target, context.peerId);
    const row = db.prepare('SELECT warns FROM users WHERE user_id=? AND chat_id=?').get(target, context.peerId);
    if (row.warns >= MAX_WARNS) {
        db.prepare('UPDATE users SET warns = 0 WHERE user_id=? AND chat_id=?').run(target, context.peerId);
        db.prepare(`INSERT OR REPLACE INTO punishments (user_id, chat_id, p_type, until) VALUES (?, ?, 'ban', 0)`).run(target, context.peerId);
        await kickUser(context.peerId, target);
        return context.send(`⚠️ Максимум варнов! @id${target} забанен.`);
    }
    await context.send(`⚠️ Варн выдан. Всего: ${row.warns}/${MAX_WARNS}`);
});

hearManager.hear(/^\/(unwarn|clearwarns)\s*(.*)/i, async (context) => {
    if (getRoleLevel(context.senderId, context.peerId) < 20) return;
    const target = await resolveTarget(context, context.match[2]);
    if (!target) return;
    if (context.match[1] === 'clearwarns') {
        db.prepare('UPDATE users SET warns = 0 WHERE user_id=? AND chat_id=?').run(target, context.peerId);
        await context.send(`✅ Варны @id${target} очищены.`);
    } else {
        db.prepare('UPDATE users SET warns = MAX(0, warns - 1) WHERE user_id=? AND chat_id=?').run(target, context.peerId);
        await context.send(`✅ 1 Варн снят с @id${target}.`);
    }
});

// === ФИЛЬТРЫ И НАСТРОЙКИ ===
hearManager.hear(/^\/(antimat|antilink|anticaps|antispam)\s+(on|off|1|0|вкл|выкл)/i, async (context) => {
    if (getRoleLevel(context.senderId, context.peerId) < 50) return context.send("❌ Доступно от Модератора.");
    const [, setting, val] = context.match;
    const isEnabled = ['on', '1', 'вкл'].includes(val.toLowerCase()) ? 1 : 0;
    db.prepare(`UPDATE settings SET ${setting.toLowerCase()} = ? WHERE chat_id = ?`).run(isEnabled, context.peerId);
    await context.send(`✅ ${setting} ${isEnabled ? 'включен' : 'выключен'}.`);
});

hearManager.hear(/^\/filter\s+(add|del|list)\s*(.*)/i, async (context) => {
    if (getRoleLevel(context.senderId, context.peerId) < 50) return;
    const [, action, word] = context.match;
    if (action === 'list') {
        const words = db.prepare('SELECT word FROM forbidden_words WHERE chat_id=?').all(context.peerId);
        return context.send("🚫 Фильтры:\n" + (words.map(w=>w.word).join(', ') || "Пусто"));
    }
    if (!word) return context.send("❌ Укажите слово.");
    if (action === 'add') { db.prepare('INSERT OR IGNORE INTO forbidden_words (chat_id, word) VALUES (?, ?)').run(context.peerId, word.toLowerCase()); await context.send(`✅ Добавлено: ${word}`); }
    else { db.prepare('DELETE FROM forbidden_words WHERE chat_id=? AND word=?').run(context.peerId, word.toLowerCase()); await context.send(`✅ Удалено: ${word}`); }
});

// === РОЛИ И НИКИ ===
hearManager.hear(/^\/setrole\s+(.*)/i, async (context) => {
    if (context.senderId !== OWNER_ID) return context.send("❌ Только владелец.");
    const args = context.match[1].split(' ');
    const role = args[args.length-1];
    const target = await resolveTarget(context, context.match[1]);
    if (target && ROLES[role]) {
        db.prepare('UPDATE users SET role=? WHERE user_id=? AND chat_id=?').run(role, target, context.peerId);
        await context.send(`✅ Роль ${role} выдана @id${target}.`);
    }
});

hearManager.hear(/^\/nick\s+(.*)/i, async (context) => {
    if (getRoleLevel(context.senderId, context.peerId) < 80) return;
    const target = await resolveTarget(context, context.match[1]);
    const nick = context.match[1].split(' ').slice(1).join(' ').trim();
    if (target && nick) { db.prepare('UPDATE users SET nickname=? WHERE user_id=? AND chat_id=?').run(nick.substring(0,30), target, context.peerId); await context.send(`✅ Ник установлен.`); }
});

// === УТИЛИТЫ (Clean, Zov, Pin, Report) ===
hearManager.hear(/^\/clean\s*(\d*)/i, async (context) => {
    if (getRoleLevel(context.senderId, context.peerId) < 20) return;
    let limit = parseInt(context.match[1]) || 10;
    if (limit > 100) limit = 100;
    const ids = (recentMessages[context.peerId] || []).slice(-limit);
    if (ids.length) {
        try { await vk.api.messages.delete({ peer_id: context.peerId, conversation_message_ids: ids, delete_for_all: 1 }); await context.send(`🧹 Удалено ${ids.length} смс.`); }
        catch(e) { await context.send("❌ Ошибка очистки."); }
    } else await context.send("❌ Нет истории сообщений.");
});

hearManager.hear(/^\/zov\s*(.*)/i, async (context) => {
    if (getRoleLevel(context.senderId, context.peerId) < 50) return;
    const members = await vk.api.messages.getConversationMembers({ peer_id: context.peerId });
    const tags = members.profiles.map(p => `[id${p.id}|&#8288;]`).slice(0, 50).join('');
    await context.send(`📢 ${context.match[1] || 'Сбор!'}\n\n${tags}`);
});

hearManager.hear(/^\/report\s*(.*)/i, async (context) => {
    if (!context.hasReplyMessage) return context.send("❌ Ответьте на сообщение нарушителя.");
    await vk.api.messages.send({ peer_id: OWNER_ID, message: `📢 ЖАЛОБА из ${context.peerId}\nОт: @id${context.senderId}\nНа: @id${context.replyMessage.senderId}\nПричина: ${context.match[1]||'Нет'}`, random_id: 0 });
    await context.send("✅ Жалоба отправлена.");
});

// === ФАН И НЕЙРОСЕТЬ (из Bot2) ===
hearManager.hear(/^\/(calc|кальк)\s+(.+)/i, async (context) => {
    const expr = context.match[2].replace(/[^-()\d/*+.]/g, '');
    try { await context.send(`🧮 Ответ: ${new Function('return ' + expr)()}`); } catch(e) { await context.send("❌ Ошибка."); }
});

hearManager.hear(/^\/ai\s+(.+)/i, async (context) => {
    await context.send("⏳ Думаю...");
    const ans = await askCerebras(context.match[1]);
    await context.send(`🤖 | AI\n━━━━━━━━━━━━━━\n\n${ans}`);
});

hearManager.hear(/^\/(quote|joke|coin|dice|choose)/i, async (context) => {
    const cmd = context.match[1].toLowerCase();
    if (cmd === 'quote') await context.send(`💬 ${['Работает — не трогай.', 'Порядок начинается с правил.'][Math.floor(Math.random()*2)]}`);
    if (cmd === 'joke') await context.send(`😄 ${['У админа нет эмоций, у него try/except.', 'Бот упал? Значит устал.'][Math.floor(Math.random()*2)]}`);
    if (cmd === 'coin') await context.send(`🪙 Выпало: ${Math.random()>0.5?'Орёл':'Решка'}`);
    if (cmd === 'dice') await context.send(`🎲 Выпало: ${Math.floor(Math.random()*6)+1}`);
    if (cmd === 'choose') {
        const opts = context.text.split(' ').slice(1).join(' ').split('|');
        if(opts.length>1) await context.send(`🤔 Я выбираю: ${opts[Math.floor(Math.random()*opts.length)].trim()}`);
    }
});

hearManager.hear(/^\/remind\s+(\w+)\s+(.+)/i, async (context) => {
    const duration = parseTime(context.match[1]);
    if (duration <= 0) return context.send("❌ Формат: 10m, 1h");
    db.prepare(`INSERT INTO reminders (user_id, chat_id, text, trigger_at) VALUES (?, ?, ?, ?)`).run(context.senderId, context.peerId, context.match[2], Math.floor(Date.now()/1000)+duration);
    await context.send(`⏰ Напомню через ${context.match[1]}.`);
});

// === ЭКОНОМИКА ===
hearManager.hear(/^\/(coins|баланс)/i, async (context) => {
    ensureUser(context.senderId, 0);
    const row = db.prepare('SELECT balance FROM users WHERE user_id=? AND chat_id=0').get(context.senderId);
    await context.send(`🪙 Баланс: ${formatCoins(row.balance)}`);
});

hearManager.hear(/^\/(daily|бонус)/i, async (context) => {
    ensureUser(context.senderId, 0);
    const p = db.prepare(`SELECT until FROM punishments WHERE user_id=? AND chat_id=0 AND p_type='daily'`).get(context.senderId);
    const now = Math.floor(Date.now()/1000);
    if (p && p.until > now) return context.send(`⏳ Бонус получен. Жди завтра.`);
    db.prepare('UPDATE users SET balance=balance+100 WHERE user_id=? AND chat_id=0').run(context.senderId);
    db.prepare(`INSERT OR REPLACE INTO punishments (user_id, chat_id, p_type, until) VALUES (?, 0, 'daily', ?)`).run(context.senderId, now+86400);
    await context.send(`🎁 Получено 100 🪙!`);
});

hearManager.hear(/^\/(slots|слоты)\s+(\d+)/i, async (context) => {
    ensureUser(context.senderId, 0);
    const bet = parseInt(context.match[2]);
    const row = db.prepare('SELECT balance FROM users WHERE user_id=? AND chat_id=0').get(context.senderId);
    if (bet <= 0 || row.balance < bet) return context.send("❌ Недостаточно монет.");
    
    const a = GAME_EMOJIS[Math.floor(Math.random()*GAME_EMOJIS.length)];
    const b = GAME_EMOJIS[Math.floor(Math.random()*GAME_EMOJIS.length)];
    const c = GAME_EMOJIS[Math.floor(Math.random()*GAME_EMOJIS.length)];
    
    let win = 0;
    if (a===b && b===c) win = bet * 5;
    else if (a===b || b===c || a===c) win = bet * 2;

    db.prepare('UPDATE users SET balance = balance + ? WHERE user_id=? AND chat_id=0').run(win - bet, context.senderId);
    await context.send(`🎰 ${a} | ${b} | ${c}\n${win ? `🎉 Победа: ${formatCoins(win)}` : `❌ Проигрыш: ${formatCoins(bet)}`}`);
});

// ГЛОБАЛЬНЫЕ КОМАНДЫ (Владелец)
hearManager.hear(/^\/groups/i, async (context) => {
    if (context.senderId !== OWNER_ID) return;
    const chats = db.prepare('SELECT DISTINCT chat_id FROM settings').all();
    await context.send(`📋 Чатов в базе: ${chats.length}\nДля выбора: /select [id]`);
});
hearManager.hear(/^\/select\s+(\d+)/i, async (context) => {
    if (context.senderId !== OWNER_ID) return;
    ownerSelectedChat = parseInt(context.match[1]);
    await context.send(`✅ Выбран чат ${ownerSelectedChat}`);
});
hearManager.hear(/^\/(gban|gmute|gkick)\s*(.*)/i, async (context) => {
    if (context.senderId !== OWNER_ID) return;
    const target = await resolveTarget(context, context.match[2]);
    if (!target) return;
    const chats = db.prepare('SELECT chat_id FROM settings').all();
    for(const c of chats) {
        if(context.match[1]==='gkick') kickUser(c.chat_id, target);
        if(context.match[1]==='gban') db.prepare(`INSERT OR REPLACE INTO punishments (user_id, chat_id, p_type, until) VALUES (?, ?, 'ban', 0)`).run(target, c.chat_id);
    }
    await context.send(`✅ Глобальное действие ${context.match[1]} выполнено.`);
});

// 7. ФОНОВЫЕ ЗАДАЧИ
setInterval(() => {
    const now = Math.floor(Date.now() / 1000);
    // Напоминания
    const rems = db.prepare(`SELECT * FROM reminders WHERE trigger_at <= ?`).all(now);
    for (const r of rems) {
        vk.api.messages.send({ peer_id: r.chat_id, message: `⏰ @id${r.user_id} Напоминание:\n${r.text}`, random_id: 0 }).catch(()=>{});
        db.prepare(`DELETE FROM reminders WHERE id = ?`).run(r.id);
    }
    // Авто-размут и авто-разбан
    const expired = db.prepare(`SELECT * FROM punishments WHERE until > 0 AND until <= ? AND p_type IN ('mute', 'ban')`).all(now);
    for (const p of expired) {
        db.prepare(`DELETE FROM punishments WHERE user_id=? AND chat_id=? AND p_type=?`).run(p.user_id, p.chat_id, p.p_type);
        vk.api.messages.send({ peer_id: p.chat_id, message: `✅ Срок ${p.p_type} истёк для @id${p.user_id}.`, random_id: 0 }).catch(()=>{});
    }
}, 5000);

console.log("🚀 Запуск MEGA NODE.JS Менеджера со всеми функциями...");
vk.updates.start().then(() => console.log("✅ Бот успешно запущен на Node.js!"));