FROM node:24-alpine
WORKDIR /app
COPY relay.mjs .
USER node
ENV PORT=8080
EXPOSE 8080
CMD ["node","relay.mjs"]
