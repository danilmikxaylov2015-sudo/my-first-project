const TelegramBot = require('node-telegram-bot-api');
const mineflayer = require('mineflayer');

// ================= НАСТРОЙКИ =================
const TOKEN = '8778362559:AAGYlu7WG0u8J9Uw_-nQbpvhIpdZW56ZxGo';
const ADMIN_ID = 8343382233; // Твой ID в телеге
// =============================================

const bot = new TelegramBot(TOKEN, { polling: true });

// Переменные для хранения данных сервера
let serverConfig = {
    ip: null,
    nick: null,
    password: null
};

// Переменная для самого игрового бота
let mcBot = null; 

// Защита от чужих
const checkAdmin = (msg) => msg.chat.id === ADMIN_ID;

// Команда /ip
bot.onText(/\/ip (.+)/, (msg, match) => {
    if (!checkAdmin(msg)) return;
    serverConfig.ip = match[1];
    bot.sendMessage(ADMIN_ID, `🌐 IP сервера сохранен: ${serverConfig.ip}`);
});

// Команда /nick
bot.onText(/\/nick (.+)/, (msg, match) => {
    if (!checkAdmin(msg)) return;
    serverConfig.nick = match[1];
    bot.sendMessage(ADMIN_ID, `👤 Ник сохранен: ${serverConfig.nick}`);
});

// Команда /password
bot.onText(/\/password (.+)/, (msg, match) => {
    if (!checkAdmin(msg)) return;
    serverConfig.password = match[1];
    bot.sendMessage(ADMIN_ID, '🔑 Пароль сохранен! (В целях безопасности я его не показываю)');
});

// Команда /start - Запуск бота на сервер
bot.onText(/\/start/, (msg) => {
    if (!checkAdmin(msg)) return;

    if (!serverConfig.ip || !serverConfig.nick || !serverConfig.password) {
        return bot.sendMessage(ADMIN_ID, '❌ Сначала укажи /ip, /nick и /password!');
    }

    if (mcBot) {
        return bot.sendMessage(ADMIN_ID, '⚠️ Бот уже находится на сервере!');
    }

    bot.sendMessage(ADMIN_ID, `⏳ Подключаюсь к ${serverConfig.ip} под ником ${serverConfig.nick}...`);

    // Создаем виртуального игрока
    mcBot = mineflayer.createBot({
        host: serverConfig.ip, // IP сервера
        username: serverConfig.nick, // Твой ник
        version: false // Автоматически определяет версию сервера
    });

    // Когда бот успешно зашел в мир
    mcBot.once('spawn', () => {
        bot.sendMessage(ADMIN_ID, '✅ Бот зашел на сервер! Авторизуюсь...');
        // Пишем команду /login в игровой чат
        mcBot.chat(`/login ${serverConfig.password}`);
    });

    // Если бота кикнули или сервер закрылся
    mcBot.on('kicked', (reason) => {
        bot.sendMessage(ADMIN_ID, `🚪 Бота кикнули. Причина: ${reason}`);
        mcBot = null;
    });

    // Если произошла ошибка сети
    mcBot.on('error', (err) => {
        bot.sendMessage(ADMIN_ID, `❌ Ошибка подключения: ${err.message}`);
        mcBot = null;
    });
});

// Команда /stop - Отключение от сервера
bot.onText(/\/stop/, (msg) => {
    if (!checkAdmin(msg)) return;

    if (!mcBot) {
        return bot.sendMessage(ADMIN_ID, '⚠️ Бот и так отключен.');
    }

    mcBot.quit(); // Выходим с сервера
    mcBot = null;
    bot.sendMessage(ADMIN_ID, '🛑 Бот отключился от сервера.');
});

// Команда /status - Проверка настроек
bot.onText(/\/status/, (msg) => {
    if (!checkAdmin(msg)) return;
    
    let text = `📊 **Текущие настройки:**\n`;
    text += `IP: ${serverConfig.ip || '❌ Не задан'}\n`;
    text += `Ник: ${serverConfig.nick || '❌ Не задан'}\n`;
    text += `Пароль: ${serverConfig.password ? '✅ Задан' : '❌ Не задан'}\n`;
    text += `Статус: ${mcBot ? '🟢 В игре' : '🔴 Оффлайн'}`;
    
    bot.sendMessage(ADMIN_ID, text, { parse_mode: 'Markdown' });
});

console.log('AFK-Бот запущен. Жду команд в Телеграме...');